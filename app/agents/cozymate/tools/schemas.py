from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.errors import ApiError


JsonSchema = dict[str, Any]


_TOOL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "load_service_skill": {
        "type": "object",
        "additionalProperties": False,
        "required": ["service_skill_id"],
        "properties": {
            "service_skill_id": {
                "type": "string",
                "enum": [
                    "birth-prep",
                    "milk-management",
                    "health-consultation",
                    "emotion-support",
                    "device-guidance",
                ],
                "description": "要加载的具体服务技能 id。",
            }
        },
    },
    "profile_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "profile_update": {
        "type": "object",
        "additionalProperties": False,
        "minProperties": 1,
        "properties": {
            "user": {
                "type": "object",
                "additionalProperties": False,
                "minProperties": 1,
                "properties": {
                    "preferred_name": {
                        "anyOf": [
                            {"type": "string", "minLength": 1, "maxLength": 120},
                            {"type": "null"},
                        ]
                    },
                    "age": {
                        "anyOf": [
                            {"type": "integer", "minimum": 12, "maximum": 70},
                            {"type": "null"},
                        ]
                    },
                    "estimated_due_date": {
                        "anyOf": [
                            {"type": "string", "format": "date"},
                            {"type": "null"},
                        ]
                    },
                },
            },
            "infants": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "minProperties": 2,
                    "required": ["infant_id"],
                    "properties": {
                        "infant_id": {"type": "string", "format": "uuid"},
                        "name": {"type": "string", "minLength": 1, "maxLength": 120},
                        "sex_at_birth": {
                            "anyOf": [
                                {
                                    "type": "string",
                                    "enum": ["female", "male", "intersex", "unknown", "undisclosed"],
                                },
                                {"type": "null"},
                            ]
                        },
                        "birth_date": {
                            "anyOf": [
                                {"type": "string", "format": "date"},
                                {"type": "null"},
                            ]
                        },
                    },
                },
            },
        },
    },
    "records_milk_summary_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 7,
                "description": "需要汇总的近期趋势天数。",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "最多纳入的近期喂养和吸奶记录数。",
            },
        },
    },
    "records_milk_status_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 7,
                "description": "用于状态分类判断的近期趋势天数。",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "最多检查的近期喂养和吸奶记录数。",
            },
        },
    },
    "records_milk_analysis_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 7,
                "description": "用于生成奶量分析快照的近期趋势天数。",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 8,
                "description": "最多纳入的近期喂养、吸奶和宝宝生长记录数。",
            },
        },
    },
    "records_milk_analysis_intake": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "resume", "answer", "reset"],
                "default": "start",
                "description": "开始、恢复、回答当前唯一问题，或明确重置六项奶量分析采集。",
            },
            "observed_answers": {
                "type": "array",
                "maxItems": 5,
                "description": "action=answer 时，列出本轮用户原话中明确回答到的一个或多个采集字段；evidence 必须逐字来自本轮消息。",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["field", "evidence"],
                    "properties": {
                        "field": {
                            "type": "string",
                            "enum": [
                                "infant_wet_diapers",
                                "infant_state_or_satisfaction",
                                "infant_growth_signal",
                                "maternal_red_flags",
                                "maternal_breast_comfort",
                            ],
                        },
                        "evidence": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                            "description": "能够支持该字段的本轮用户原话片段，不改写、不推断。",
                        },
                    },
                },
            },
        },
    },
    "records_milk_analysis_evaluate": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "records_growth_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "infant_id": {"type": "string", "maxLength": 80},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "最多返回的宝宝生长记录数。",
            },
        },
    },
    "plans_current_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "最多纳入的当前计划和任务数。",
            }
        },
    },
    "plans_calendar_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_date": {"type": "string", "maxLength": 20},
            "status": {"type": "string", "maxLength": 32},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
                "description": "最多返回的日程任务数。",
            },
        },
    },
    "pregnancy_diary_query": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "entry_date": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
                "description": "读取单日日记的 YYYY-MM-DD 日期；省略时按日期范围列出日记。",
            },
            "start_date": {"type": "string", "maxLength": 20, "description": "日期范围起点，格式为 YYYY-MM-DD。"},
            "end_date": {"type": "string", "maxLength": 20, "description": "日期范围终点，格式为 YYYY-MM-DD。"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 7,
                "description": "最多返回的日记条数。",
            },
        },
    },
    "pregnancy_diary_save": {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "content"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["create", "update"],
                "description": "创建新日记或完整重写已有日记。",
            },
            "entry_date": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
                "description": "YYYY-MM-DD 日期；省略时使用当前用户本地日期。",
            },
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 5000,
                "description": "完整正文；update 必须包含旧正文和本轮新增事实的完整重写结果。",
            },
        },
    },
    "pregnancy_diary_delete": {
        "type": "object",
        "additionalProperties": False,
        "required": ["entry_date", "confirmation_evidence"],
        "properties": {
            "entry_date": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
                "description": "要删除的 YYYY-MM-DD 日期。",
            },
            "confirmation_evidence": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "逐字引用当前用户消息中表达删除确认的短原文。",
            },
        },
    },
    "devices_pump_status_read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "最多纳入的吸奶器设备和遥测事件数。",
            }
        },
    },
    "devices_guidance_read": {
        "type": "object",
        "additionalProperties": False,
        "required": ["model"],
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 10,
                "description": "最多纳入的已打包设备指导素材数。",
            },
            "content_type": {
                "type": "string",
                "maxLength": 80,
                "description": "可选内容类型过滤条件，例如 application/pdf 或 video/mp4。",
            },
            "model": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "description": "已经由用户确认的设备型号；当前支持 Air1 或 BP334。",
            },
            "topic": {
                "type": "string",
                "maxLength": 80,
                "description": "可选指导主题，例如 setup、cleaning、flange、suction 或 bluetooth。",
            },
            "step": {
                "type": "string",
                "maxLength": 80,
                "description": "需要读取的明确指导步骤，例如 guide.parts、guide.charging 或 guide.assembly。",
            },
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "可选关键词过滤条件。",
            },
            "measured_nipple_mm": {
                "type": "number",
                "minimum": 0,
                "description": "用户提供的乳头根部测量值，供回复时按官方素材核对。",
            },
        },
    },
    "devices_unboxing_advance": {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "action"],
        "properties": {
            "model": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "description": "已经由用户确认的设备型号；当前支持 Air1 或 BP334。",
            },
            "action": {
                "type": "string",
                "enum": ["start", "resume", "complete_current", "cancel"],
                "description": "开始新流程、恢复当前流程、确认完成当前主步骤，或取消流程。",
            },
        },
    },
    "conversation_history_image_load": {
        "type": "object",
        "additionalProperties": False,
        "required": ["image_url"],
        "properties": {
            "image_url": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "当前可见对话历史中由智能体此前回复展示过的目标图片 URL。",
            },
            "detail": {
                "type": "string",
                "enum": ["low", "high"],
                "default": "low",
                "description": "普通内容识别使用 low；小字或精细结构识别使用 high。",
            },
        },
    },
    "plans_milk_plan_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["direction"],
        "properties": {
            "direction": {"type": "string", "enum": ["increase", "maintain", "decrease"]},
            "start_date": {"type": "string", "maxLength": 20},
            "days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7},
            "target_daily_ml": {
                "type": "number",
                "minimum": 0,
                "maximum": 5000,
                "description": "仅当用户明确给出阶段日目标时传入；未给出时由 runtime 根据近 7 天实测吸奶均值保守计算。",
            },
            "preferred_pumping_times": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^(?:[01]?\\d|2[0-3]):[0-5]\\d$"},
                "description": "仅传用户明确指定的可执行吸奶时间；未指定时由 runtime 复用近期节奏。",
            },
            "calendar_write_strategy": {
                "type": "string",
                "enum": ["append", "replace_future_plan_tasks"],
                "description": "未来已有奶量任务且用户明确选择后传入；不能替用户默认追加或替换。",
            },
        },
    },
    "plans_milk_schedule_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["plan_id"],
        "properties": {
            "plan_id": {"type": "string", "format": "uuid"},
            "target_date": {"type": "string", "format": "date"},
            "target_dates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 7,
                "uniqueItems": True,
                "items": {"type": "string", "format": "date"},
            },
            "busy_windows": {
                "type": "array",
                "minItems": 1,
                "maxItems": 21,
                "description": "已经存在于其它日程、仅用于避让且本轮不重复创建的不可用时段。",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["start_time", "end_time"],
                    "properties": {
                        "date": {"type": "string", "format": "date"},
                        "start_time": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
                        "end_time": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
                        "title": {"type": "string", "maxLength": 120},
                    },
                },
            },
            "calendar_events": {
                "type": "array",
                "minItems": 1,
                "maxItems": 21,
                "description": "用户本轮明确新增并希望同步到日程的生活事项；runtime 会同时把它作为不可用时段参与重排。",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["date", "start_time", "end_time", "title"],
                    "properties": {
                        "date": {"type": "string", "format": "date"},
                        "start_time": {"type": "string", "pattern": "^(?:[01]?\\d|2[0-3]):[0-5]\\d$"},
                        "end_time": {"type": "string", "pattern": "^(?:[01]?\\d|2[0-3]):[0-5]\\d$"},
                        "title": {"type": "string", "minLength": 1, "maxLength": 120},
                        "description": {"type": "string", "maxLength": 500},
                    },
                },
            },
            "min_gap_minutes": {"type": "integer", "minimum": 0, "maximum": 360, "default": 90},
            "default_duration_minutes": {"type": "integer", "minimum": 1, "maximum": 240, "default": 30},
        },
    },
    "pregnancy_plan_workflow": {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "enum": [
                    "start_or_resume",
                    "submit_form",
                    "answer_current",
                    "edit_answer",
                    "pause",
                    "resume",
                    "abandon",
                    "generate_plan",
                ],
                "description": "本次只执行一个状态机命令；不要自行推测或传入内部阶段。",
            },
            "choice_id": {
                "type": "string",
                "maxLength": 120,
                "description": "当前步骤展示的稳定选项 id；自由文本回答时可省略。",
            },
            "answer": {
                "type": "string",
                "maxLength": 2000,
                "description": "用户本轮明确提供的自由文本答案；不得填入模型推断。",
            },
            "step_id": {
                "type": "string",
                "maxLength": 120,
                "description": "仅 edit_answer 使用，必须来自工具返回的可编辑步骤 id。",
            },
            "restart": {
                "type": "boolean",
                "description": "仅当用户明确要求放弃当前采集并重新开始时为 true。",
            },
            "summary": {"type": "string", "maxLength": 2000},
            "scope": {"type": "string", "enum": ["full", "prenatal_only", "short_range"]},
            "additional_info": {
                "type": "string",
                "maxLength": 2000,
                "description": "生成阶段用户已确认的最后补充；没有补充时省略。",
            },
        },
    },
    "plans_task_create_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "plan_id": {"type": "string", "maxLength": 80},
            "task_date": {"type": "string", "maxLength": 20},
            "task_time": {"type": "string", "maxLength": 16},
            "title": {"type": "string", "minLength": 1, "maxLength": 255},
            "description": {"type": "string", "maxLength": 2000},
            "payload": {
                "type": "object",
                "additionalProperties": True,
            },
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "plans_task_complete_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id"],
        "properties": {
            "task_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "completed": {"type": "boolean", "default": True},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "pregnancy_plan_todo_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["plan_id", "item_id", "completed", "expected_version"],
        "properties": {
            "plan_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "item_id": {"type": "string", "minLength": 1, "maxLength": 160},
            "completed": {"type": "boolean"},
            "expected_version": {"type": "integer", "minimum": 1},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "plans_task_update_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id"],
        "properties": {
            "task_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "plan_id": {"type": "string", "maxLength": 80},
            "task_date": {"type": "string", "maxLength": 20},
            "task_time": {"type": "string", "maxLength": 16},
            "title": {"type": "string", "maxLength": 255},
            "description": {"type": "string", "maxLength": 2000},
            "payload": {"type": "object", "additionalProperties": True},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "plans_task_delete_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id"],
        "properties": {
            "task_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "reason": {"type": "string", "maxLength": 500},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "plans_plan_delete_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["plan_id"],
        "properties": {
            "plan_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "reason": {"type": "string", "maxLength": 500},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "notifications_milk_reminder_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 255},
            "body": {"type": "string", "maxLength": 2000},
            "remind_at": {"type": "string", "maxLength": 80},
            "payload": {
                "type": "object",
                "additionalProperties": True,
            },
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "records_feeding_record_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["feed_time", "feed_type"],
        "properties": {
            "infant_id": {"type": "string", "maxLength": 80},
            "feed_time": {"type": "string", "minLength": 1, "maxLength": 80},
            "feed_type": {"type": "string", "minLength": 1, "maxLength": 32},
            "feed_action": {"type": "string", "maxLength": 32},
            "volume_ml": {"type": "number", "minimum": 0},
            "duration_seconds": {"type": "integer", "minimum": 0},
            "title": {"type": "string", "maxLength": 255},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "records_pumping_record_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["pump_start_time"],
        "properties": {
            "pump_start_time": {"type": "string", "minLength": 1, "maxLength": 80},
            "pump_end_time": {"type": "string", "maxLength": 80},
            "milk_volume_ml": {"type": "number", "minimum": 0},
            "pump_type": {"type": "string", "maxLength": 32},
            "duration_seconds": {"type": "integer", "minimum": 0},
            "source": {"type": "string", "maxLength": 32},
            "title": {"type": "string", "maxLength": 255},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "records_feeding_record_delete_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["record_id"],
        "properties": {
            "record_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "reason": {"type": "string", "maxLength": 500},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "records_growth_record_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["measured_at"],
        "properties": {
            "infant_id": {"type": "string", "maxLength": 80},
            "measured_at": {"type": "string", "minLength": 1, "maxLength": 80},
            "height_cm": {"type": "number", "minimum": 0},
            "weight_kg": {"type": "number", "minimum": 0},
            "head_cm": {"type": "number", "minimum": 0},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "records_growth_record_update_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["record_id"],
        "properties": {
            "record_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "infant_id": {"type": "string", "maxLength": 80},
            "measured_at": {"type": "string", "maxLength": 80},
            "height_cm": {"type": "number", "minimum": 0},
            "weight_kg": {"type": "number", "minimum": 0},
            "head_cm": {"type": "number", "minimum": 0},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "birth_plan_form_create": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "hospital_bag_cart_update": {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "replace_pump_model",
                    "add_pump_model",
                    "optimize_budget",
                    "remove_items",
                    "restore_items",
                    "replace_items",
                    "mark_provided",
                    "mark_owned",
                    "update_quantity",
                    "reset_cart",
                    "clarify",
                ],
            },
            "item_ids": {
                "type": "array",
                "maxItems": 40,
                "items": {"type": "string", "maxLength": 120},
            },
            "product_sku_id": {"type": ["string", "null"], "maxLength": 120},
            "quantity_updates": {
                "type": "array",
                "maxItems": 40,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["item_id", "qty"],
                    "properties": {
                        "item_id": {"type": "string", "maxLength": 120},
                        "qty": {"type": "integer", "minimum": 0, "maximum": 99},
                    },
                },
            },
            "target_budget": {"type": ["number", "null"], "minimum": 0},
            "budget_mode": {"type": "string", "enum": ["under", "around", "cheaper", "minimal", "none"]},
            "preference": {
                "type": "string",
                "enum": [
                    "balanced",
                    "cheapest",
                    "comfort",
                    "breastfeeding",
                    "minimal",
                    "budget",
                    "portable",
                    "performance",
                    "app",
                    "simple",
                    "premium",
                ],
            },
            "preserve_item_ids": {
                "type": "array",
                "maxItems": 40,
                "items": {"type": "string", "maxLength": 120},
            },
            "allow_remove_pump": {"type": "boolean"},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "hospital_bag_pump_recommend": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "requested_model": {"type": ["string", "null"], "maxLength": 120},
            "use_case": {
                "type": "string",
                "enum": [
                    "unknown",
                    "hospital_backup",
                    "daily_home",
                    "work_pumping",
                    "portable",
                    "comfort",
                    "performance",
                    "high_output",
                    "budget",
                ],
            },
            "feeding_intention": {"type": "string", "enum": ["unknown", "breastfeeding", "mixed", "formula"]},
            "preference": {
                "type": "string",
                "enum": [
                    "balanced",
                    "cheapest",
                    "comfort",
                    "breastfeeding",
                    "minimal",
                    "budget",
                    "portable",
                    "performance",
                    "app",
                    "simple",
                    "premium",
                ],
            },
            "target_budget_usd": {"type": ["number", "null"], "minimum": 0},
            "must_have_app": {"type": ["boolean", "null"]},
            "need_single_unit": {"type": ["boolean", "null"]},
        },
    },
    "support_ticket_propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["issue_summary", "user_confirmed"],
        "properties": {
            "issue_type": {
                "type": "string",
                "enum": [
                    "malfunction",
                    "missing_parts",
                    "defect",
                    "warranty",
                    "return_or_refund",
                    "order_or_shipping",
                    "usage_help",
                    "safety_concern",
                    "other",
                ],
            },
            "issue_summary": {
                "type": "string",
                "description": "面向客服和用户的简洁问题摘要，用于预填工单。",
                "minLength": 1,
                "maxLength": 2000,
            },
            "product_model": {"type": "string", "maxLength": 120},
            "order_number": {"type": "string", "maxLength": 120},
            "purchase_channel": {"type": "string", "maxLength": 120},
            "user_contact": {"type": "string", "maxLength": 255},
            "troubleshooting_done": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
            },
            "urgency": {"type": "string", "enum": ["normal", "high", "safety"]},
            "user_emotion": {"type": "string", "maxLength": 500},
            "attachments_note": {"type": "string", "maxLength": 1000},
            "user_confirmed": {
                "type": "boolean",
                "description": "只有用户明确同意现在创建售后工单时才传 true。",
            },
            "locale": {"type": "string", "maxLength": 35},
        },
    },
    "ibclc_consult_card_create": {
        "type": "object",
        "additionalProperties": False,
        "required": ["reason"],
        "properties": {
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            "feeding_context": {"type": "string", "maxLength": 2000},
            "urgency": {"type": "string", "enum": ["routine", "soon", "urgent"]},
            "preferred_language": {"type": "string", "maxLength": 80},
            "payload": {"type": "object", "additionalProperties": True},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
        },
    },
}


for _tool_name in ("records_pumping_record_delete_propose", "records_growth_record_delete_propose"):
    _TOOL_INPUT_SCHEMAS[_tool_name] = _TOOL_INPUT_SCHEMAS["records_feeding_record_delete_propose"]

_TOOL_INPUT_SCHEMAS["plans_milk_task_update_propose"] = _TOOL_INPUT_SCHEMAS["plans_task_update_propose"]
_TOOL_INPUT_SCHEMAS["plans_milk_task_delete_propose"] = _TOOL_INPUT_SCHEMAS["plans_task_delete_propose"]

for _tool_name in ("labor_communication_card_create", "hospital_bag_form_create", "hospital_bag_card_create"):
    _TOOL_INPUT_SCHEMAS[_tool_name] = _TOOL_INPUT_SCHEMAS["birth_plan_form_create"]

_TOOL_INPUT_SCHEMAS["hospital_bag_card_create"] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "generation_mode": {"type": "string", "enum": ["standard", "quick", "immediate"]},
    },
}


def input_schema_for_tool(tool_name: str) -> JsonSchema:
    schema = _TOOL_INPUT_SCHEMAS.get(tool_name)
    if schema is None:
        raise ApiError(code="tool_schema_not_found", message="Tool input schema is not registered.", status=500)
    return deepcopy(schema)


def input_schema_tool_names() -> tuple[str, ...]:
    return tuple(sorted(_TOOL_INPUT_SCHEMAS))
