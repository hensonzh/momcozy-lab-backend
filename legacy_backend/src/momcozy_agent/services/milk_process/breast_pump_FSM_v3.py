# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Tuple

"""
模块: breast_pump_FSM_v3.2

变更说明 (v2 → v3):
    - 彻底移除跨请求共享的状态机实例，解决 FastAPI 多线程竞争问题。
    - 状态机快照（含 RealtimeEnvelopeFilter 内部状态）全部序列化写入 JSONL 日志。
    - 每次请求：加锁 → 从 log 末行读快照 → 重建状态机 → step_second() → 写回快照 → 释放锁。
    - 使用 asyncio.Lock（per user+side），同一用户同一侧请求严格串行，不同用户并发。
    - SessionManager.process() 改为 async，FastAPI 路由须使用 async def + await。
    - step 新增 "pause"：暂停期间 tick/t_main/P 原地锁定，不调用 step_second()。
      再次收到 "running" 时从暂停快照无缝续跑，不补偿暂停期间的时间空档。
    - step 新增 "offline"：设备断联。
        · 不操作状态机，不加锁，不影响对侧。
        · 写入轻量日志（server_time + step="offline" + output.process=0），无 sm_state。
        · 单侧 offline → error=0，综合进程=对侧进程值。
        · 双侧均 offline（仅双设备模式下双侧同时 offline）→ error=-1，所有进程=0。
        · 单设备模式（只传一侧）offline → error=0，不触发 error=-1。
        · 设备重连后发 step="start" 重新开启会话，offline 后不接受 running。
    - error 字段语义：E1/E2/E3（奶阵冻结）不触发 error=-1，改为 error=0 + text 描述；
      error=-1 仅保留给系统异常及双侧均 offline。
    - 日志文件名只保留日期（YYYYmmdd），不含具体时分秒。
    - 日志每条记录用服务端时间戳 server_time 标记写入时刻，不再存前端 time 字段。

变更说明 (v3 → v3.1):
    - S_SILENT 提前触发阈值：30秒 → 3秒（TICKS_3S = 60 tick）。
    - S_SILENT 新增退回逻辑：条件B提前触发进入 S_BURST 后，若 t_main 尚未到达
      t_initial 且连续 5秒（TICKS_10S = 100 tick）检测到 "00"，则退回 S_SILENT；
      P 保留不回退，phase_idx 不变，继续等待同一奶阵。
      条件A（时间）触发的奶阵不允许退回。
    - S_BURST 冻结触发阈值：60秒 → 30秒（TICKS_30S = 600 tick）。
    - S_BURST 解冻触发阈值：30秒 → 3秒（TICKS_3S = 60 tick）。
    - 新增标志位 burst_early_trigger：记录当前奶阵是否由条件B提前触发。
    - 新增方法 _go_silent_early()：专用于条件B退回，不切换 phase_idx。

变更说明 (v3.1 → v3.2 默认模式):
    - 引入 DefaultModeStateMachine：当用户配置 JSON 不存在时启用默认模式。
    - 默认模式规则（每秒 20 tick，MER 为 "01"/"11"/"00"）：
        · 奶阵标记连续 20 次（1秒）识别到 "01" 或 "11" → 进程增速 = 每秒 +0.12。
        · 奶阵标记连续 20 次（1秒）识别到 "00"       → 进程增速 = 每秒 +0.02。
        · 进程 P ∈ [0,1]，process = round(P * 100)，上限 100。
    - 读取到用户配置文件时仍使用原迟滞状态机 BreastPumpStateMachine。
    - sm_state 中新增字段 mode：
        · BreastPumpStateMachine.dump_state(): mode="FSM"。
        · DefaultModeStateMachine.dump_state(): mode="DEFAULT"。
      用于区分快照类型，避免两种模式之间互相 load_state。

FastAPI 接入示例:
    from fastapi import FastAPI
    from breast_pump_FSM_v3_2 import LogDrivenSessionManager

    app = FastAPI()
    manager = LogDrivenSessionManager(json_dir="configs", log_dir="logs")

    @app.post("/pump/process")
    async def pump_process(payload: dict):
        return await manager.process(payload)
"""

# ═══════════════════════════════════════════════════════════════════════════════
# 全局常量
# ═══════════════════════════════════════════════════════════════════════════════

FS = 20
DT = 1.0 / FS
TICKS_PER_SEC = FS

TICKS_3S  = 3  * FS   #  60 tick — S_SILENT 提前触发 & S_BURST 解冻阈值
TICKS_10S = 5  * FS   # 100 tick — S_SILENT 退回阈值（条件B提前触发后，5秒）
TICKS_30S = 30 * FS   # 600 tick — S_BURST 冻结触发阈值
BURST_WARMUP_TICKS = 10 * FS

SENSITIVITY = 0.5

# 默认模式参数
DEFAULT_MER_THRESH = 20          # 连续 20 次（1秒）
DEFAULT_SPEED_FAST = 0.0018        # 每秒 +0.12
DEFAULT_SPEED_SLOW = 0.0002        # 每秒 +0.02

_MER_INT_TO_STR: Dict[int, str] = {0: "00", 1: "01", 3: "11"}

# ═══════════════════════════════════════════════════════════════════════════════
# 状态枚举
# ═══════════════════════════════════════════════════════════════════════════════

class State(Enum):
    S_SILENT = auto()
    S_BURST = auto()
    FINISHED = auto()

class DefaultState(Enum):
    DEFAULT = auto()   # 默认模式的逻辑状态，仅用于 state.name

# ═══════════════════════════════════════════════════════════════════════════════
# 参数数据类
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class BurstParam:
    index: int
    t_initial: float
    t_terminate: float
    zp: float

@dataclass(slots=True)
class SilentParam:
    key: str
    zp: float
    t_start: float
    t_end: float

# ═══════════════════════════════════════════════════════════════════════════════
# 实时包络滤波器
# ═══════════════════════════════════════════════════════════════════════════════

class RealtimeEnvelopeFilter:
    MEDFILT_K = 5
    AMP_WINDOW = 20
    ENV_WINDOW = 40
    SM_WINDOW = 40
    ZERO_THR = 1e-6

    def __init__(self) -> None:
        self._last_valid = 0.0
        self._med_buf: deque = deque(maxlen=self.MEDFILT_K)
        self._amp_buf: deque = deque(maxlen=self.AMP_WINDOW)
        self._amp_max = 0.0
        self._amp_min = 0.0
        self._amp_ready = False
        self._pk_alpha = 2.0 / (self.ENV_WINDOW + 1.0)
        self._pk_env = 0.0
        self._sm_alpha = 2.0 / (self.SM_WINDOW + 1.0)
        self._sm_env = 0.0

    def reset(self) -> None:
        self._last_valid = 0.0
        self._med_buf.clear()
        self._amp_buf.clear()
        self._amp_max = 0.0
        self._amp_min = 0.0
        self._amp_ready = False
        self._pk_env = 0.0
        self._sm_env = 0.0

    def _recompute_amp_range(self) -> None:
        self._amp_max = max(self._amp_buf)
        self._amp_min = min(self._amp_buf)

    def step(self, raw: float) -> float:
        if abs(raw) < self.ZERO_THR:
            raw = self._last_valid
        else:
            self._last_valid = raw

        self._med_buf.append(raw)
        degl = sorted(self._med_buf)[len(self._med_buf) // 2]

        window_was_full = len(self._amp_buf) == self._amp_buf.maxlen
        if window_was_full:
            old = self._amp_buf[0]
            need_recompute = (old >= self._amp_max) or (old <= self._amp_min)
        else:
            need_recompute = False

        self._amp_buf.append(degl)

        if need_recompute:
            self._recompute_amp_range()
        else:
            if degl > self._amp_max:
                self._amp_max = degl
            if degl < self._amp_min:
                self._amp_min = degl

        if not self._amp_ready:
            if len(self._amp_buf) >= self.AMP_WINDOW:
                self._amp_ready = True
                self._recompute_amp_range()
            else:
                return 0.0

        amp = max(0.0, self._amp_max - self._amp_min)

        if amp >= self._pk_env:
            self._pk_env = amp
        else:
            self._pk_env += self._pk_alpha * (amp - self._pk_env)

        self._sm_env += self._sm_alpha * (self._pk_env - self._sm_env)
        return self._sm_env

    def dump_state(self) -> dict:
        return {
            "last_valid": self._last_valid,
            "med_buf": list(self._med_buf),
            "amp_buf": list(self._amp_buf),
            "amp_max": self._amp_max,
            "amp_min": self._amp_min,
            "amp_ready": self._amp_ready,
            "pk_env": self._pk_env,
            "sm_env": self._sm_env,
        }

    def load_state(self, snap: dict) -> None:
        self._last_valid = snap["last_valid"]
        self._med_buf = deque(snap["med_buf"], maxlen=self.MEDFILT_K)
        self._amp_buf = deque(snap["amp_buf"], maxlen=self.AMP_WINDOW)
        self._amp_max = snap["amp_max"]
        self._amp_min = snap["amp_min"]
        self._amp_ready = snap["amp_ready"]
        self._pk_env = snap["pk_env"]
        self._sm_env = snap["sm_env"]

# ═══════════════════════════════════════════════════════════════════════════════
# 状态机主体（迟滞模式）
# ═══════════════════════════════════════════════════════════════════════════════

class BreastPumpStateMachine:
    """迟滞型规则状态机，单侧吸乳过程建模。"""

    @classmethod
    def from_json(cls, json_path: str) -> "BreastPumpStateMachine":
        p = Path(json_path)
        if not p.exists():
            raise FileNotFoundError(f"参数文件未找到: {p.resolve()}")
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        params = {k: v for k, v in raw.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)}
        return cls(**params)

    def __init__(self, **kw) -> None:
        self.T_ref = kw["T_ref"]
        self.T_Ending = kw["T_Ending"]

        pat = re.compile(r"^T_burst(\d+)_initial$")
        ids = {int(m.group(1)) for k in kw if (m := pat.match(k))}
        if not ids:
            raise ValueError("JSON 中未找到 T_burst{k}_initial 字段")
        self.N = max(ids)

        self.burst_params: List[BurstParam] = [
            BurstParam(k, kw[f"T_burst{k}_initial"],
                       kw[f"T_burst{k}_terminate"], kw[f"ZP_burst{k}"])
            for k in range(1, self.N + 1)
        ]

        self.silent_params: List[SilentParam] = [
            SilentParam("01", kw.get("ZP_silent01", 0.0),
                        0.0, self.burst_params[0].t_initial)
        ]
        for k in range(1, self.N + 1):
            ts = self.burst_params[k - 1].t_terminate
            te = self.burst_params[k].t_initial if k < self.N else self.T_Ending
            self.silent_params.append(
                SilentParam(f"{k}{k+1}",
                            kw.get(f"ZP_silent{k}{k+1}", 0.0),
                            ts, te)
            )

        self.v_ref: List[float] = [
            bp.zp / (bp.t_terminate - bp.t_initial)
            if bp.t_terminate > bp.t_initial else 0.0
            for bp in self.burst_params
        ]
        self.silent_velocity: List[float] = [
            sp.zp / (sp.t_end - sp.t_start)
            if sp.t_end > sp.t_start else 0.0
            for sp in self.silent_params
        ]

        self.env_filter = RealtimeEnvelopeFilter()
        self._reset_runtime()

    def _reset_runtime(self) -> None:
        self.tick = 0
        self.t_main = 0.0
        self.P = 0.0
        self.error_codes: List[str] = []
        self.cnt_01_11 = 0
        self.cnt_00 = 0
        self.state = State.S_SILENT
        self.phase_idx = 0
        self.stage_P = 0.0
        self.burst_frozen = False
        self.burst_early_trigger = False
        self._burst_env_sum = 0.0
        self._burst_env_count = 0
        self._burst_tick = 0
        self.env_filter.reset()

    def dump_state(self) -> dict:
        return {
            "mode": "FSM",  # 标记迟滞模式
            "tick": self.tick,
            "t_main": self.t_main,
            "P": self.P,
            "error_codes": list(self.error_codes),
            "cnt_01_11": self.cnt_01_11,
            "cnt_00": self.cnt_00,
            "state": self.state.name,
            "phase_idx": self.phase_idx,
            "stage_P": self.stage_P,
            "burst_frozen": self.burst_frozen,
            "burst_early_trigger": self.burst_early_trigger,
            "_burst_env_sum": self._burst_env_sum,
            "_burst_env_count": self._burst_env_count,
            "_burst_tick": self._burst_tick,
            "env_filter": self.env_filter.dump_state(),
        }

    def load_state(self, snap: dict) -> None:
        self.tick = snap["tick"]
        self.t_main = snap["t_main"]
        self.P = snap["P"]
        self.error_codes = list(snap["error_codes"])
        self.cnt_01_11 = snap["cnt_01_11"]
        self.cnt_00 = snap["cnt_00"]
        self.state = State[snap["state"]]
        self.phase_idx = snap["phase_idx"]
        self.stage_P = snap["stage_P"]
        self.burst_frozen = snap["burst_frozen"]
        self.burst_early_trigger = snap.get("burst_early_trigger", False)
        self._burst_env_sum = snap["_burst_env_sum"]
        self._burst_env_count = snap["_burst_env_count"]
        self._burst_tick = snap["_burst_tick"]
        self.env_filter.load_state(snap["env_filter"])

    def step_second(self, raw_caps: List[float],
                    mer_strs: List[str]) -> Tuple[float, str, List[str], float]:
        if len(raw_caps) != TICKS_PER_SEC or len(mer_strs) != TICKS_PER_SEC:
            raise ValueError(f"raw_caps 和 mer_strs 长度均须为 {TICKS_PER_SEC}")
        env_sum = 0.0
        for raw, mer in zip(raw_caps, mer_strs):
            env_sum += self._step_tick(raw, mer)
        return self.P, self.state.name, list(self.error_codes), env_sum / TICKS_PER_SEC

    def _step_tick(self, raw_cap: float, mer_str: str) -> float:
        env_val = self.env_filter.step(raw_cap)

        if self.state == State.FINISHED or self.t_main >= self.T_Ending:
            self.state = State.FINISHED
            self.tick += 1
            self.t_main = self.tick * DT
            return env_val

        self._update_mer(mer_str)

        if self.state == State.S_SILENT:
            self._do_silent(env_val, mer_str)
        elif self.state == State.S_BURST:
            self._do_burst(env_val, mer_str)

        self.tick += 1
        self.t_main = self.tick * DT
        return env_val

    def _update_mer(self, m: str) -> None:
        if m in ("01", "11"):
            self.cnt_00 = 0
            self.cnt_01_11 += 1
        else:
            self.cnt_01_11 = 0
            self.cnt_00 += 1

    def _reset_mer(self) -> None:
        self.cnt_01_11 = 0
        self.cnt_00 = 0

    def _do_silent(self, env_val: float, mer_str: str) -> None:
        sp = self.silent_params[self.phase_idx]
        vel = self.silent_velocity[self.phase_idx]

        if self.stage_P < sp.zp:
            delta = min(vel * DT, sp.zp - self.stage_P)
            self.stage_P += delta
            self.P = min(self.P + delta, 1.0)

        bi = self.phase_idx
        if bi < self.N:
            bp = self.burst_params[bi]
            if self.t_main >= bp.t_initial:
                # 条件A：时间触发，不允许退回
                self.burst_early_trigger = False
                self._go_burst(bi)
            elif mer_str in ("01", "11") and self.cnt_01_11 >= TICKS_3S:
                # 条件B：MER 提前触发，允许退回
                self.burst_early_trigger = True
                self._go_burst(bi)

    def _do_burst(self, env_val: float, mer_str: str) -> None:
        bi = self.phase_idx
        bp = self.burst_params[bi]
        ec = f"E{bp.index}"

        if self.t_main >= bp.t_terminate:
            nsi = bi + 1
            if nsi < len(self.silent_params):
                self._go_silent(nsi)
            return

        # 条件B 提前触发且 t_initial 尚未到达：检测是否需要退回静默
        if self.burst_early_trigger and self.t_main < bp.t_initial:
            if mer_str == "00" and self.cnt_00 >= TICKS_10S:
                self._go_silent_early(bi)
                return

        # 冻结：连续 30 秒无奶
        if not self.burst_frozen:
            if mer_str == "00" and self.cnt_00 >= TICKS_30S:
                self.burst_frozen = True
                if ec not in self.error_codes:
                    self.error_codes.append(ec)
        else:
            # 解冻：连续 3 秒有奶
            if mer_str in ("01", "11") and self.cnt_01_11 >= TICKS_3S:
                self.burst_frozen = False
                if ec in self.error_codes:
                    self.error_codes.remove(ec)

        if not self.burst_frozen and self.stage_P < bp.zp:
            self._burst_tick += 1

            if env_val > 1e-9:
                self._burst_env_sum += env_val
                self._burst_env_count += 1

            t_rem = bp.t_terminate - self.t_main
            p_rem = bp.zp - self.stage_P
            v_ref_dyn = p_rem / t_rem if t_rem > DT else p_rem / DT

            if self._burst_tick <= BURST_WARMUP_TICKS:
                v = v_ref_dyn
            elif self._burst_env_count == 0 or self._burst_env_sum < 1e-9:
                v = v_ref_dyn
            else:
                burst_mean = self._burst_env_sum / self._burst_env_count
                deviation = (env_val - burst_mean) / burst_mean
                v = max(0.0, v_ref_dyn * (1.0 + SENSITIVITY * deviation))

            delta = min(v * DT, p_rem)
            self.stage_P += delta
            self.P = min(self.P + delta, 1.0)

    def _go_burst(self, i: int) -> None:
        self.state = State.S_BURST
        self.phase_idx = i
        self.stage_P = 0.0
        self.burst_frozen = False
        self._burst_env_sum = 0.0
        self._burst_env_count = 0
        self._burst_tick = 0
        self._reset_mer()

    def _go_silent(self, i: int) -> None:
        if self.phase_idx < self.N:
            ec = f"E{self.burst_params[self.phase_idx].index}"
            if ec in self.error_codes:
                self.error_codes.remove(ec)
        self.state = State.S_SILENT
        self.phase_idx = i
        self.stage_P = 0.0
        self.burst_frozen = False
        self.burst_early_trigger = False
        self._reset_mer()

    def _go_silent_early(self, i: int) -> None:
        """
        条件B提前触发后的退回专用方法。
        与 _go_silent() 的区别：
          - phase_idx 不变（退回到同一静默阶段，继续等待该奶阵）
          - P 不回退（已涨的进程保留）
          - stage_P 重置为 0（本阶段进程累计清零）
        """
        ec = f"E{self.burst_params[i].index}"
        if ec in self.error_codes:
            self.error_codes.remove(ec)
        self.state = State.S_SILENT
        self.stage_P = 0.0
        self.burst_frozen = False
        self.burst_early_trigger = False
        self._reset_mer()

    def get_status(self) -> dict:
        bm = self._burst_env_sum / self._burst_env_count if self._burst_env_count > 0 else 0.0
        v_ref_dyn = 0.0
        if self.state == State.S_BURST:
            bp = self.burst_params[self.phase_idx]
            t_rem = bp.t_terminate - self.t_main
            p_rem = bp.zp - self.stage_P
            v_ref_dyn = p_rem / t_rem if t_rem > DT else p_rem / DT
        return {
            "tick": self.tick,
            "t": round(self.t_main, 3),
            "P": round(self.P, 6),
            "state": self.state.name,
            "phase_idx": self.phase_idx,
            "stage_P": round(self.stage_P, 6),
            "v_ref_dyn": round(v_ref_dyn, 8),
            "burst_mean": round(bm, 6),
            "burst_tick": self._burst_tick,
            "in_warmup": self.state == State.S_BURST and self._burst_tick <= BURST_WARMUP_TICKS,
            "cnt_01_11": self.cnt_01_11,
            "cnt_00": self.cnt_00,
            "burst_frozen": self.burst_frozen,
            "burst_early_trigger": self.burst_early_trigger,
            "errors": list(self.error_codes),
        }

    def __repr__(self) -> str:
        s = self.get_status()
        extra = ", warmup" if s["in_warmup"] else ""
        extra += f", FROZEN{s['errors']}" if s["burst_frozen"] else ""
        extra += ", early" if s["burst_early_trigger"] else ""
        return f"SM(N={self.N}, {s['state']}, t={s['t']}s, P={s['P']:.3f}{extra})"

# ═══════════════════════════════════════════════════════════════════════════════
# 默认模式状态机（无配置文件时使用）
# ═══════════════════════════════════════════════════════════════════════════════

class DefaultModeStateMachine:
    """
    默认模式：无用户 JSON 配置时启用。
    规则：
        - 奶阵标记连续 20 次 "01"/"11" → 每秒 +0.12。
        - 奶阵标记连续 20 次 "00"      → 每秒 +0.02。
        - P ∈ [0,1]，process = round(P*100)，上限 100。
    """

    def __init__(self) -> None:
        self.tick = 0
        self.t_main = 0.0
        self.P = 0.0
        self.error_codes: List[str] = []
        self.cnt_active = 0   # 连续 01/11 计数
        self.cnt_idle = 0     # 连续 00 计数
        self.speed = 0.0      # 当前每秒增速（0.12 或 0.02）
        self.state = DefaultState.DEFAULT
        self.env_filter = RealtimeEnvelopeFilter()
        self.env_filter.reset()

    def _reset_runtime(self) -> None:
        self.tick = 0
        self.t_main = 0.0
        self.P = 0.0
        self.error_codes = []
        self.cnt_active = 0
        self.cnt_idle = 0
        self.speed = 0.0
        self.state = DefaultState.DEFAULT
        self.env_filter.reset()

    def dump_state(self) -> dict:
        return {
            "mode": "DEFAULT",
            "tick": self.tick,
            "t_main": self.t_main,
            "P": self.P,
            "cnt_active": self.cnt_active,
            "cnt_idle": self.cnt_idle,
            "speed": self.speed,
            "state": self.state.name,
            "env_filter": self.env_filter.dump_state(),
        }

    def load_state(self, snap: dict) -> None:
        self.tick = snap["tick"]
        self.t_main = snap["t_main"]
        self.P = snap["P"]
        self.cnt_active = snap.get("cnt_active", 0)
        self.cnt_idle = snap.get("cnt_idle", 0)
        self.speed = snap.get("speed", 0.0)
        state_name = snap.get("state", "DEFAULT")
        self.state = DefaultState[state_name] if state_name in DefaultState.__members__ else DefaultState.DEFAULT
        if "env_filter" in snap:
            self.env_filter.load_state(snap["env_filter"])
        else:
            self.env_filter.reset()

    def step_second(self, raw_caps: List[float],
                    mer_strs: List[str]) -> Tuple[float, str, List[str], float]:
        if len(raw_caps) != TICKS_PER_SEC or len(mer_strs) != TICKS_PER_SEC:
            raise ValueError(f"raw_caps 和 mer_strs 长度均须为 {TICKS_PER_SEC}")
        env_sum = 0.0

        for raw, mer in zip(raw_caps, mer_strs):
            env_val = self.env_filter.step(raw)
            env_sum += env_val

            if mer in ("01", "11"):
                self.cnt_active += 1
                self.cnt_idle = 0
            elif mer == "00":
                self.cnt_idle += 1
                self.cnt_active = 0
            else:
                self.cnt_idle += 1
                self.cnt_active = 0

            if self.cnt_active >= DEFAULT_MER_THRESH:
                self.speed = DEFAULT_SPEED_FAST
            elif self.cnt_idle >= DEFAULT_MER_THRESH:
                self.speed = DEFAULT_SPEED_SLOW

            if self.speed > 0.0 and self.P < 1.0:
                self.P = min(1.0, self.P + self.speed * DT)

            self.tick += 1
            self.t_main = self.tick * DT

        env_mean = env_sum / TICKS_PER_SEC
        return self.P, self.state.name, list(self.error_codes), env_mean

# ═══════════════════════════════════════════════════════════════════════════════
# 输出字段名常量 & 错误描述映射
# ═══════════════════════════════════════════════════════════════════════════════

KEY_ERROR = "error"
KEY_TEXT = "text"
KEY_PROCESS_L = "process_l"
KEY_PROCESS_R = "process_r"
KEY_PROCESS_ALL = "process_all"

_ERROR_DESC: Dict[str, str] = {
    "E1": "奶阵集团1期间奶断超过60s，进程暂停",
    "E2": "奶阵集团2期间奶断超过60s，进程暂停",
    "E3": "奶阵集团3期间奶断超过60s，进程暂停",
}

def _make_error_text(errors: List[str]) -> str:
    if not errors:
        return ""
    parts = [_ERROR_DESC.get(e, f"未知错误码 {e}") for e in errors]
    return "; ".join(parts)

class _BusinessError(Exception):
    """业务逻辑错误：error=0，仅写入 text 提示，不触发 error=-1。"""

# ═══════════════════════════════════════════════════════════════════════════════
# v3 核心：Log 驱动的会话管理器（无内存状态机实例）
# ═══════════════════════════════════════════════════════════════════════════════

class LogDrivenSessionManager:
    """
    v3 会话管理器：状态机快照全部持久化在 JSONL 日志，彻底消除多线程竞争。

    step 取值说明：
        "start"   : 重置状态机，开始新会话。
        "running" : 从末行快照恢复，调用 step_second()，推进 tick/t_main/P。
        "pause"   : 从末行快照恢复，不调用 step_second()，tick/t_main/P 原地锁定。
        "stop"    : 不推进计算，写入最终日志后清理会话锁；重复发 stop 幂等返回。
        "offline" : 设备断联。
                    · 不操作状态机，不加锁，不影响对侧。
                    · 写入轻量日志（仅含 server_time / step / output.process=0）。
                    · offline 之后不接受 running，设备重连须发 start 重新开启会话。
                    · 单侧 offline → error=0，综合进程=对侧进程值。
                    · 双侧均 offline（双设备模式）→ error=-1，所有进程=0。
                    · 单设备模式（只传一侧）offline → error=0，不触发 error=-1。

    日志文件命名规则：
        {log_dir}/{user_id}_{YYYYmmdd}_{side}.jsonl
        log_tag = 当天日期，每次请求实时生成。同一天续写同一文件。

    FastAPI 路由必须使用 async def + await，否则 asyncio.Lock 失效。
    """

    def __init__(self, json_dir: str = "configs", log_dir: str = "logs") -> None:
        self._json_dir = Path(json_dir)
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        self._locks: Dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

        print(f"[LogDrivenSessionManager] 初始化完成 | json_dir={self._json_dir} | log_dir={self._log_dir}")

    def _lock_key(self, user_id: str, side: str) -> str:
        return f"{user_id}:{side}"

    async def _get_lock(self, user_id: str, side: str) -> asyncio.Lock:
        key = self._lock_key(user_id, side)
        async with self._meta_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _log_path(self, user_id: str, side: str, log_tag: str) -> Path:
        return self._log_dir / f"{user_id}_{log_tag}_{side}.jsonl"

    def _read_last_snapshot(self, log_path: Path) -> Optional[dict]:
        if not log_path.exists():
            return None
        last_snap = None
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if "sm_state" in row:
                            last_snap = row["sm_state"]
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            print(f"[LogDrivenSessionManager] 读取日志失败: {e}")
        return last_snap

    def _read_last_row(self, log_path: Path) -> dict:
        if not log_path.exists():
            return {}
        last: dict = {}
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            print(f"[LogDrivenSessionManager] 读取日志行失败: {e}")
        return last

    def _append_log(self, log_path: Path, row: dict) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                json.dump(row, f, ensure_ascii=False)
                f.write("\n")
        except OSError as e:
            print(f"[LogDrivenSessionManager] 日志写入失败: {e}")

    async def _process_side(
        self,
        user_id: str,
        side: str,
        device: dict,
    ) -> Tuple[int, str, bool, bool]:
        step = device["step"]
        side_cn = "左" if side == "left" else "右"

        if step == "offline":
            log_tag = datetime.now().strftime("%Y%m%d")
            log_path = self._log_path(user_id, side, log_tag)
            self._append_log(log_path, {
                "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "step": "offline",
                "output": {"process": 0},
            })
            print(f"[LogDrivenSessionManager] 用户 {user_id} {side_cn}侧 OFFLINE | 已写日志")
            return 0, f"[{side_cn}] 设备断联", True, False

        lock = await self._get_lock(user_id, side)
        key = self._lock_key(user_id, side)

        async with lock:
            cap_data = [float(v) for v in device.get("cap_data", [])]
            mer_val = _MER_INT_TO_STR.get(int(device.get("milk_reel", 0)), "00")
            mer_list = [mer_val] * TICKS_PER_SEC

            log_tag = datetime.now().strftime("%Y%m%d")
            log_path = self._log_path(user_id, side, log_tag)

            if step == "stop":
                last_row = self._read_last_row(log_path)
                if last_row.get("step") == "stop":
                    out = last_row.get("output", {})
                    return out.get("process", 0), "", False, False

            json_path = self._json_dir / f"{user_id}_{side}.json"
            config_exists = json_path.exists()
            if config_exists:
                sm = BreastPumpStateMachine.from_json(str(json_path))
                is_default = False
            else:
                sm = DefaultModeStateMachine()
                is_default = True

            if step == "start":
                sm._reset_runtime()
            else:
                snap = self._read_last_snapshot(log_path)
                last_step = self._read_last_row(log_path).get("step")
                if last_step == "offline":
                    raise _BusinessError(
                        f"{side_cn}侧设备处于断联状态，请先发送 step=start 重新开启会话"
                    )
                if snap is not None and last_step != "stop":
                    snap_mode = snap.get("mode")
                    if is_default:
                        if snap_mode == "DEFAULT":
                            sm.load_state(snap)
                        else:
                            sm._reset_runtime()
                    else:
                        if snap_mode == "DEFAULT":
                            sm._reset_runtime()
                        else:
                            sm.load_state(snap)
                else:
                    sm._reset_runtime()
                    if snap is None:
                        print(
                            f"[LogDrivenSessionManager] 警告: 用户 {user_id} {side_cn}侧 "
                            f"快照未找到，从头计算（降级模式）"
                        )

            if step in ("pause", "stop"):
                P = sm.P
                state_name = sm.state.name
                errors = list(sm.error_codes)
                env_mean = getattr(sm.env_filter, "_sm_env", 0.0)

                process = int(round(P * 100))
                text = _make_error_text(errors)

                self._append_log(log_path, {
                    "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "step": step,
                    "cap_data": cap_data,
                    "milk_reel": device.get("milk_reel"),
                    "bandpower": device.get("bandpower"),
                    "milk": device.get("milk"),
                    "output": {
                        "P": P,
                        "process": process,
                        "state": state_name,
                        "error_codes": errors,
                        "env_mean": round(env_mean, 6),
                    },
                    "sm_state": sm.dump_state(),
                })

                print(
                    f"[LogDrivenSessionManager] 用户 {user_id} {side_cn}侧 {step.upper()} "
                    f"| t={sm.t_main:.1f}s P={P:.4f}"
                )

            else:
                if len(cap_data) != TICKS_PER_SEC:
                    raise ValueError(f"cap_data 长度须为 {TICKS_PER_SEC}，当前为 {len(cap_data)}")

                P, state_name, errors, env_mean = sm.step_second(cap_data, mer_list)
                process = int(round(P * 100))
                text = _make_error_text(errors)

                self._append_log(log_path, {
                    "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "step": step,
                    "cap_data": cap_data,
                    "milk_reel": device.get("milk_reel"),
                    "bandpower": device.get("bandpower"),
                    "milk": device.get("milk"),
                    "output": {
                        "P": P,
                        "process": process,
                        "state": state_name,
                        "error_codes": errors,
                        "env_mean": round(env_mean, 6),
                    },
                    "sm_state": sm.dump_state(),
                })

        if step == "stop":
            async with self._meta_lock:
                self._locks.pop(key, None)
            print(f"[LogDrivenSessionManager] 用户 {user_id} {side_cn}侧 STOP | 锁已释放，会话结束")

        return process, text, False, False

    async def process(self, payload: dict) -> dict:
        user_id = payload.get("user_id")
        if not user_id:
            raise ValueError("请求缺少 user_id 字段")

        result: dict = {"user_id": user_id}
        process_l: Optional[int] = None
        process_r: Optional[int] = None
        error_flag = 0
        texts: List[str] = []
        offline_sides: List[str] = []

        tasks = {}
        if "device_left" in payload:
            tasks["left"] = asyncio.create_task(
                self._process_side(user_id, "left", payload["device_left"])
            )
        if "device_right" in payload:
            tasks["right"] = asyncio.create_task(
                self._process_side(user_id, "right", payload["device_right"])
            )

        for side, task in tasks.items():
            try:
                proc, text, is_offline, _ = await task
                if side == "left":
                    process_l = proc
                    result[KEY_PROCESS_L] = proc
                else:
                    process_r = proc
                    result[KEY_PROCESS_R] = proc
                if is_offline:
                    offline_sides.append(side)
                if text:
                    texts.append(text)
            except _BusinessError as exc:
                side_cn = "左" if side == "left" else "右"
                msg = str(exc)
                texts.append(msg)
                print(f"[LogDrivenSessionManager] 业务错误: {msg}")
                if side == "left":
                    result[KEY_PROCESS_L] = 0
                else:
                    result[KEY_PROCESS_R] = 0
            except Exception as exc:
                error_flag = -1
                side_cn = "左" if side == "left" else "右"
                msg = f"[{side_cn}] 计算异常: {exc}"
                texts.append(msg)
                print(f"[LogDrivenSessionManager] 系统异常: {msg}")
                if side == "left":
                    result[KEY_PROCESS_L] = -1
                else:
                    result[KEY_PROCESS_R] = -1

        all_sides = list(tasks.keys())
        both_sides_present = len(all_sides) == 2
        all_offline = len(offline_sides) == len(all_sides) and len(all_sides) > 0

        if both_sides_present and all_offline:
            error_flag = -1
            texts = ["设备断联异常"]
            result[KEY_PROCESS_ALL] = 0
        else:
            valid = [
                p for s, p in [("left", process_l), ("right", process_r)]
                if p is not None and p >= 0 and s not in offline_sides
            ]
            result[KEY_PROCESS_ALL] = int(round(sum(valid) / len(valid))) if valid else -1

        result[KEY_ERROR] = error_flag
        result[KEY_TEXT] = "; ".join(texts)
        return result

    async def remove_session(self, user_id: str) -> None:
        for side in ("left", "right"):
            key = self._lock_key(user_id, side)
            async with self._meta_lock:
                self._locks.pop(key, None)
        print(f"[LogDrivenSessionManager] 用户 {user_id} 会话已强制清理")


if __name__ == "__main__":
    print("breast_pump_FSM_v3.2 模块加载成功。")
    print("step 取值: start | running | pause | stop | offline")
    print("FastAPI 路由必须使用 async def + await manager.process(payload)")
