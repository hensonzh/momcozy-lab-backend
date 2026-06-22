from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..services import data_store, profile_write_queue
from ..types import RuntimeInputs

HOSPITAL_BAG_CART_URL = "/hospital-bag-cart"
HOSPITAL_BAG_CART_LINK = f"[打开待产包购物车]({HOSPITAL_BAG_CART_URL})"
PUMP_ITEM = {
    "label": "便携式吸奶器",
    "quantity": "1台",
    "priority": "recommended",
    "note": "如果计划母乳或混合喂养，可作为初期涨奶或追奶的备用选择；具体使用以医院和哺乳顾问建议为准。",
    "explain": "涨奶、排奶或回家后储奶时备用。",
}
HOSPITAL_BAG_CART_ASSISTANT_FOLLOWUP = {
    "kind": "hospital_bag_cart",
    "message": (
        "待产包清单我整理好了。\n\n"
        "我顺手把清单里适合放入购物车参考的妈妈/宝宝用品整理好了，"
        "不用一次买完，先看清单里的优先级，按实际情况删减后再决定是否购买。\n\n"
        f"**{HOSPITAL_BAG_CART_LINK}**"
    ),
}
DEFAULT_HOSPITAL_BAG_CART_GROUPS: list[dict[str, Any]] = [
    {
        "title": "妈妈护理",
        "tone": "rose",
        "items": [
            {"id": "mom-pad", "name": "产褥垫组合装", "desc": "入院与产后前几天使用", "qty": 1, "price": 59.9, "keywords": ["产褥垫", "护理垫"]},
            {"id": "mom-sanitary", "name": "产妇卫生巾", "desc": "夜用加长款，按住院天数准备", "qty": 1, "price": 39.9, "keywords": ["卫生巾"]},
            {"id": "mom-underwear", "name": "一次性内裤", "desc": "高腰柔软，产后更方便更换", "qty": 1, "price": 49.9, "keywords": ["内裤", "一次性内裤"]},
            {"id": "mom-wipes", "name": "产后护理湿巾", "desc": "温和清洁，适合住院随身包", "qty": 1, "price": 29.9, "keywords": ["湿巾", "护理湿巾"]},
            {"id": "mom-bottle", "name": "产后冲洗瓶", "desc": "产后清洁更方便，是否带去医院按医院建议", "qty": 1, "price": 39.9, "keywords": ["冲洗瓶"]},
            {"id": "mom-briefs", "name": "高腰收腹内裤", "desc": "不压腹，更适合产后恢复期穿着", "qty": 1, "price": 69.9, "keywords": ["收腹", "高腰"]},
        ],
    },
    {
        "title": "宝宝出院",
        "tone": "mint",
        "items": [
            {"id": "baby-diaper", "name": "新生儿纸尿裤", "desc": "NB 码小包装，避免带太多", "qty": 1, "price": 59.9, "keywords": ["纸尿裤", "尿不湿"]},
            {"id": "baby-wipes", "name": "婴儿柔湿巾", "desc": "无香精，适合换尿裤场景", "qty": 1, "price": 29.9, "keywords": ["婴儿湿巾", "柔湿巾"]},
            {"id": "baby-towel", "name": "棉柔巾", "desc": "洗脸、擦手、护理都可用", "qty": 1, "price": 29.9, "keywords": ["棉柔巾"]},
            {"id": "baby-blanket", "name": "宝宝出院包被", "desc": "柔软包裹，按季节搭配外层", "qty": 1, "price": 129.0, "keywords": ["包被"]},
            {"id": "baby-clothes", "name": "新生儿连体衣礼盒", "desc": "出院和回家第一周可替换穿", "qty": 1, "price": 159.0, "keywords": ["连体衣", "衣服", "礼盒"]},
            {"id": "baby-bath-towel", "name": "婴儿浴巾", "desc": "洗澡、包裹和保暖都可用", "qty": 1, "price": 59.9, "keywords": ["浴巾"]},
        ],
    },
    {
        "title": "母乳喂养",
        "tone": "sky",
        "items": [
            {"id": "milk-pad", "name": "防溢乳垫", "desc": "母乳或混合喂养可先备小包装", "qty": 1, "price": 39.9, "keywords": ["防溢乳垫", "乳垫"]},
            {"id": "milk-cream", "name": "乳头护理霜", "desc": "哺乳初期不适时可咨询后使用", "qty": 1, "price": 49.9, "keywords": ["乳头霜", "护理霜"]},
            {"id": "milk-storage", "name": "储奶袋", "desc": "返家后储奶备用，住院可少量准备", "qty": 1, "price": 49.9, "keywords": ["储奶袋"]},
            {"id": "milk-pump", "name": "便携式吸奶器", "desc": "可选备用项，是否带去医院先问医院", "qty": 1, "price": 699.0, "keywords": ["吸奶器"]},
            {"id": "milk-bra", "name": "哺乳文胸", "desc": "产后和哺乳初期更舒适", "qty": 1, "price": 159.0, "keywords": ["哺乳文胸", "文胸"]},
            {"id": "milk-bottle", "name": "宽口径奶瓶", "desc": "混合喂养或返家后备用", "qty": 1, "price": 89.9, "keywords": ["奶瓶"]},
        ],
    },
]
MOMCOZY_PUMP_OFFICIAL_COLLECTION_URL = "https://momcozy.com/collections/wearable-breast-pump"
MOMCOZY_PUMP_OFFICIAL_OVERVIEW_URL = "https://momcozy.com/collections/electric-breast-pump"
MOMCOZY_PUMP_SUPPORT_GUIDE_URL = "https://support.momcozy.com/article/56837165211801"
HOSPITAL_BAG_CART_USD_TO_CNY_RATE = 6.8
MOMCOZY_PUMP_IMAGE_URLS = {
    "milk-pump": "https://momcozy.com/cdn/shop/files/MomcozyMoblieFlow_BreastPump_7.jpg?v=1776163451",
    "pump-s9-pro": "https://momcozy.com/cdn/shop/files/S9pro_3ff43646-b9be-4b4d-a8d8-918834d887a0.jpg?v=1699943493",
    "pump-s12-pro-quick": "https://momcozy.com/cdn/shop/files/1.1_a787cf6f-0656-44dc-86e9-0a4f75846cbc.jpg?v=1776744837",
    "pump-m5-smart": "https://momcozy.com/cdn/shop/files/1_b8f691a7-6acf-44dc-acbc-ed6bf82e8a9d.jpg?v=1760428066",
    "pump-m6": "https://momcozy.com/cdn/shop/files/01_56489eac-5396-4685-aacc-3c6c14c39930.jpg?v=1775025039",
    "pump-v1-pro": "https://momcozy.com/cdn/shop/files/lQDPJws-Zh7cFX_NBdrNBLCwLR71U5WToVIG-TXf9_H4AA_1200_1498.jpg?v=1755159282",
    "pump-v2-pro": "https://momcozy.com/cdn/shop/files/v2pro-4.png?v=1779353933",
    "pump-m9": "https://momcozy.com/cdn/shop/files/MomcozyMoblieFlow_BreastPump_7.jpg?v=1776163451",
    "pump-w1": "https://momcozy.com/cdn/shop/files/1._1_app2.jpg?v=1777030861",
    "pump-air-1": "https://momcozy.com/cdn/shop/files/MomcozyAir1Ultra-slimBreastPump_1.png?v=1740971384",
}
HOSPITAL_BAG_CART_PRODUCT_IMAGE_URLS = {
    "mom-pad": "https://momcozy.com/cdn/shop/files/01_e3747022-14ff-4a12-a503-044276f89265.webp?v=1779352318",
    "mom-sanitary": "https://momcozy.com/cdn/shop/files/01_e3747022-14ff-4a12-a503-044276f89265.webp?v=1779352318",
    "mom-underwear": "https://momcozy.com/cdn/shop/files/PK006-1_-1.png?v=1779351545",
    "mom-wipes": "https://momcozy.com/cdn/shop/files/12b.jpg?v=1779353160",
    "mom-bottle": "https://momcozy.com/cdn/shop/files/01_e3747022-14ff-4a12-a503-044276f89265.webp?v=1779352318",
    "mom-briefs": "https://momcozy.com/cdn/shop/files/b58ce7b99582c961375527c3c6b27ebb_9023fff7-0f2e-42c1-864e-fd2a7a732e72.png?v=1779351709",
    "baby-diaper": "https://babycozy.com/cdn/shop/files/1.1_8e53efbf-da7d-4997-a472-3a335bedbf31.jpg?v=1711696033",
    "baby-wipes": "https://momcozy.com/cdn/shop/files/12b.jpg?v=1779353160",
    "baby-towel": "https://momcozy.com/cdn/shop/files/1_d4e469a4-e733-4cf1-8863-c3c5256eca2a.jpg?v=1779353358",
    "baby-blanket": "https://momcozy.com/cdn/shop/files/1-1_abb6b92a-ac07-4b73-b3a5-7d995989b721.jpg?v=1779353389",
    "baby-blanket-basic": "https://momcozy.com/cdn/shop/files/1_7732d4e1-f5a7-4a75-8f7e-c888eed37548.jpg?v=1779352173",
    "baby-clothes": "https://momcozy.com/cdn/shop/files/1_f348eb24-2845-4b7b-8cff-6f78510fa1fa.webp?v=1779422192",
    "baby-bath-towel": "https://momcozy.com/cdn/shop/files/yujin.jpg?v=1779353668",
    "milk-pad": "https://momcozy.com/cdn/shop/files/5_dbb18aeb-7e96-4c95-a1a7-5a9378875b7b.jpg?v=1779352202",
    "milk-cream": "https://momcozy.com/cdn/shop/files/MomcozyNippleCreramforBreastfeeding_8.jpg?v=1779352434",
    "milk-storage": "https://momcozy.com/cdn/shop/files/13_5b267527-b032-4c66-bec8-21c675a15007.webp?v=1779430748",
    "milk-bra": "https://momcozy.com/cdn/shop/files/yn21_ae9a5331-7afc-4dde-abc1-5136e356bcbe.jpg?v=1736236303",
    "milk-bottle": "https://momcozy.com/cdn/shop/files/619nKXnpNKL._SL1500.jpg?v=1779354235",
}
MOMCOZY_PUMP_PRODUCT_CATALOG: list[dict[str, Any]] = [
    {
        "sku_id": "pump-s9-pro",
        "model": "S9 Pro",
        "name": "Momcozy S9 Pro 便携式吸奶器",
        "price_usd": 64.99,
        "sale_price_usd": 58.49,
        "tier": "entry",
        "use_cases": ["budget", "hospital_backup", "daily_home"],
        "preferences": ["budget", "simple", "balanced"],
        "best_for": "预算优先、想先备一台简单可靠的入门款。",
        "features": ["长续航", "LED 显示", "低噪"],
        "suction": "最高 -285 mmHg",
        "battery": "约 8-9 次",
        "weight": "250 g",
        "noise": "≤45 dB",
        "app": False,
        "supports_single_unit": True,
    },
    {
        "sku_id": "pump-s12-pro-quick",
        "model": "S12 Pro Quick",
        "name": "Momcozy S12 Pro Quick 可穿戴吸奶器",
        "price_usd": 74.99,
        "sale_price_usd": 67.49,
        "tier": "entry_plus",
        "use_cases": ["budget", "hospital_backup", "daily_home", "comfort"],
        "preferences": ["budget", "comfort", "balanced"],
        "best_for": "第一次准备吸奶器、希望好上手和清洁方便。",
        "features": ["新手友好", "清洁方便", "节省时间"],
        "suction": "最高 -292 mmHg",
        "battery": "约 7-8 次",
        "weight": "",
        "noise": "≤46 dB",
        "app": False,
        "supports_single_unit": True,
    },
    {
        "sku_id": "pump-m5-smart",
        "model": "M5 Smart",
        "name": "Momcozy M5 Smart 可穿戴吸奶器",
        "price_usd": 119.99,
        "tier": "mid",
        "use_cases": ["daily_home", "portable", "work_pumping"],
        "preferences": ["portable", "app", "balanced"],
        "best_for": "想要轻一些、日常使用并希望手机 App 调节。",
        "features": ["轻量", "App 控制", "日常通勤友好"],
        "suction": "最高 -285 mmHg",
        "battery": "约 4-5 次",
        "weight": "230 g",
        "noise": "≤48 dB",
        "app": True,
        "supports_single_unit": True,
    },
    {
        "sku_id": "pump-m6",
        "model": "M6",
        "name": "Momcozy M6 Mobile Style 轻薄吸奶器",
        "price_usd": 129.99,
        "sale_price_usd": 116.99,
        "tier": "mid_plus",
        "use_cases": ["daily_home", "comfort", "balanced"],
        "preferences": ["comfort", "balanced", "performance"],
        "best_for": "日常使用频率较高，想要舒适和输出更均衡。",
        "features": ["舒适贴合", "稳定输出", "轻薄隐蔽"],
        "suction": "最高 -295 mmHg",
        "battery": "约 5-6 次",
        "weight": "293 g",
        "noise": "≤48 dB",
        "app": False,
        "supports_single_unit": True,
    },
    {
        "sku_id": "pump-v1-pro",
        "model": "V1 Pro",
        "name": "Momcozy V1 Pro 医院级可穿戴吸奶器",
        "price_usd": 199.99,
        "sale_price_usd": 179.99,
        "tier": "pro",
        "use_cases": ["daily_home", "performance", "high_output"],
        "preferences": ["performance", "battery", "balanced"],
        "best_for": "更看重医院级吸力和长续航，主要在家高频使用。",
        "features": ["医院级吸力", "长续航", "15 档吸力"],
        "suction": "最高 -300 mmHg",
        "battery": "约 7-9 次",
        "weight": "280 g 电机",
        "noise": "≤53 dB",
        "app": False,
        "supports_single_unit": False,
    },
    {
        "sku_id": "pump-v2-pro",
        "model": "V2 Pro",
        "name": "Momcozy V2 Pro 医院级可穿戴吸奶器",
        "price_usd": 199.99,
        "sale_price_usd": 169.99,
        "tier": "pro",
        "use_cases": ["daily_home", "performance", "high_output", "portable"],
        "preferences": ["performance", "portable", "balanced"],
        "best_for": "想要医院级吸力，同时更在意轻量电机和外出便携。",
        "features": ["医院级吸力", "超轻电机", "低噪便携"],
        "suction": "最高 -288 mmHg",
        "battery": "约 4-6 次",
        "weight": "127 g 电机",
        "noise": "≤48 dB",
        "app": False,
        "supports_single_unit": False,
    },
    {
        "sku_id": "pump-m9",
        "model": "M9",
        "name": "Momcozy M9 Mobile Flow 智能吸奶器",
        "price_usd": 159.99,
        "sale_price_usd": 143.99,
        "tier": "pro_app",
        "use_cases": ["work_pumping", "portable", "performance", "high_output"],
        "preferences": ["app", "performance", "portable"],
        "best_for": "上班、外出或高频吸奶，希望效率高并用 App 做个性化控制。",
        "features": ["强吸力", "App 控制", "个性化节律"],
        "suction": "最高 -300 mmHg",
        "battery": "约 4-5 次",
        "weight": "302 g",
        "noise": "≤42 dB",
        "app": True,
        "supports_single_unit": True,
    },
    {
        "sku_id": "pump-w1",
        "model": "W1",
        "name": "Momcozy W1 暖感按摩可穿戴吸奶器",
        "price_usd": 329.99,
        "tier": "premium_comfort",
        "use_cases": ["comfort", "daily_home"],
        "preferences": ["comfort", "premium"],
        "best_for": "预算更宽松，并且特别在意暖感按摩和舒适感。",
        "features": ["暖感按摩", "Milk Boost 模式", "透明顶盖"],
        "suction": "最高 -295 mmHg",
        "battery": "约 10 次",
        "weight": "325 g",
        "noise": "≤50 dB",
        "app": True,
        "supports_single_unit": False,
    },
    {
        "sku_id": "pump-air-1",
        "model": "Air 1",
        "name": "Momcozy Air 1 超薄吸奶器",
        "price_usd": 369.99,
        "tier": "premium_portable",
        "use_cases": ["work_pumping", "portable", "discreet"],
        "preferences": ["portable", "app", "premium"],
        "best_for": "预算充足，最在意职场/外出场景里的轻薄隐蔽；这是高价轻薄升级款，不是降预算选择。",
        "features": ["超薄", "充电盒", "App 控制"],
        "suction": "最高 -285 mmHg",
        "battery": "约 6-7 次，配充电盒约 15 次",
        "weight": "260 g",
        "noise": "≤45 dB",
        "app": True,
        "supports_single_unit": False,
    },
]
HOSPITAL_BAG_CART_PUMP_ITEM_ID = "milk-pump"
HOSPITAL_BAG_CART_BUDGET_REMOVE_ORDER = [
    "baby-clothes",
    "mom-briefs",
    "milk-bra",
    "milk-storage",
    "baby-bath-towel",
    "mom-bottle",
    "milk-bottle",
    "milk-cream",
    "mom-wipes",
    "milk-pad",
    "baby-towel",
]
HOSPITAL_BAG_CART_LIGHT_BUDGET_REMOVE_IDS = {"baby-clothes", "mom-briefs", "milk-bra"}
HOSPITAL_BAG_CART_PROTECTED_ITEM_IDS = {
    "mom-pad",
    "mom-sanitary",
    "mom-underwear",
    "baby-diaper",
    "baby-wipes",
    "baby-blanket",
    "baby-blanket-basic",
    HOSPITAL_BAG_CART_PUMP_ITEM_ID,
}
HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS: dict[str, dict[str, Any]] = {
    "baby-blanket": {
        "id": "baby-blanket-basic",
        "name": "基础款宝宝包被",
        "desc": "先选基础款，按季节再加外层",
        "qty": 1,
        "price": 59.9,
        "keywords": ["包被"],
    }
}
HOSPITAL_BAG_CART_REPLACEMENT_ORIGINAL_BY_ID = {
    replacement["id"]: original_id
    for original_id, replacement in HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS.items()
}
HOSPITAL_BAG_ITEM_EXPLANATIONS = [
    (("产褥垫", "产妇卫生巾"), "产后恶露量较多，用来垫床或替代普通卫生巾。"),
    (("胎监带",), "做胎心监护时固定探头用，有些医院要求自带。"),
    (("吸管杯",), "产后或宫缩时不方便起身，躺着喝水更省力。"),
    (("哺乳文胸", "哺乳背心"), "方便产后喂奶，也比普通内衣更不勒。"),
    (("防溢乳垫",), "放在内衣里吸收漏奶，避免衣服被打湿。"),
    (("便携式吸奶器", "吸奶器"), "涨奶、排奶或回家后储奶时备用。"),
    (("储奶袋", "储奶瓶"), "用来保存挤出的母乳，住院期少量准备即可。"),
    (("乳头霜",), "哺乳初期乳头干痛时可用，先少量准备。"),
    (("乳盾",), "套在乳头上的辅助亲喂用品，是否需要先听专业建议。"),
    (("哺乳枕",), "喂奶时托住宝宝和手臂，不是必须。"),
    (("收腹带",), "产后腹部支撑用品，剖宫产尤其要先问医生。"),
    (("安全提篮", "安全座椅"), "宝宝出院坐车时使用，提前确认交通方式。"),
    (("奶瓶清洁用品",), "用来清洗奶瓶、奶嘴或吸奶配件，住院只需少量。"),
    (("消毒设备",), "回家后消毒奶瓶或吸奶配件用，住院不一定带大件。"),
    (("喂养记录工具",), "记录吃奶、排尿排便和睡眠，方便家人同步。"),
    (("分娩沟通单",), "记录生产偏好和需要提前沟通的事，入院时方便给医护看。"),
]
BIRTH_PLAN_DISCLAIMER = (
    "这份沟通单只用于沟通。请优先遵循医生和医院建议，尤其是因安全原因需要调整计划时。"
)
BIRTH_PLAN_ASSISTANT_FOLLOWUP = {
    "kind": "birth_plan_card_guidance",
    "message": "你可以提前和医院确认，并在产检或入院前把这份沟通单给医生/护士看，用它快速沟通你的重点偏好和需要讨论的问题。",
}
BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT = 20
REMOVED_HOSPITAL_BAG_FORM_FIELD_IDS = {
    "hospital_rules_or_notes",
    "existing_checklist_or_photo_note",
}
EXCLUSIVE_BIRTH_PLAN_MULTI_SELECT_OPTIONS = {
    "我还没想好，请帮我整理成温和版本",
    "不需要持续解释，必要时再说就好",
    "无特别偏好，听医生安排",
    "听医生判断即可",
    "灌肠/剃毛：希望按医院常规即可",
    "暂未决定，听医生建议",
    "未确定",
    "还没确定",
    "还没想好",
}
PLACEHOLDER_VALUES = {
    "",
    "to confirm",
    "待确认",
    "未确定",
    "不确定",
    "不清楚",
    "不太清楚",
    "说不清楚",
    "不了解",
    "还不确定",
    "还没确定",
    "还没想好",
    "不知道",
    "跳过",
    "暂不提供",
    "暂时不说",
    "不想说",
    "none",
    "n/a",
}
BIRTH_PATH_ALIASES = {
    "vaginal": "顺产",
    "natural": "顺产",
    "顺产": "顺产",
    "planned_c_section": "剖宫产",
    "c_section": "剖宫产",
    "c-section": "剖宫产",
    "cesarean": "剖宫产",
    "剖宫产": "剖宫产",
    "计划剖宫产": "剖宫产",
    "剖腹产": "剖宫产",
    "刨腹产": "剖宫产",
}
HOSPITAL_BAG_FORM_FIELDS = [
    {
        "id": "due_date_or_week",
        "label": "基本信息｜预产期或当前孕周",
        "type": "text",
        "required": True,
        "placeholder": "例如：2026-06-12 或 37 周",
    },
    {
        "id": "first_birth",
        "label": "基本信息｜是否第一胎",
        "type": "select",
        "required": True,
        "options": ["是", "否"],
    },
    {
        "id": "fetus_count",
        "label": "基本信息｜这次是单胎、双胎，还是三胎及以上？",
        "type": "select",
        "required": True,
        "options": ["单胎", "双胎", "三胎及以上", "不确定"],
    },
    {
        "id": "pregnancy_history_or_notes",
        "label": "基本信息｜医生是否提示过特殊情况",
        "type": "multi_select",
        "required": True,
        "allow_other_input": True,
        "other_placeholder": "请简单填写医生提示的情况",
        "options": [
            "没有",
            "妊娠糖尿病",
            "血压或子痫前期风险",
            "胎盘问题",
            "早产风险",
            "宝宝可能 NICU",
            "其它",
        ],
    },
    {
        "id": "birth_path",
        "label": "生产信息｜分娩方式",
        "type": "select",
        "required": True,
        "options": ["顺产", "剖宫产", "还不确定"],
    },
    {
        "id": "feeding_intention",
        "label": "喂养信息｜喂养意向",
        "type": "select",
        "required": True,
        "options": ["亲喂母乳", "配方奶", "混合喂养", "还不确定"],
    },
    {
        "id": "return_to_work_timing",
        "label": "喂养信息｜产后多久返工",
        "type": "text",
        "required": True,
        "placeholder": "例如：6 周后、3 个月后、暂不返工",
    },
    {
        "id": "support_person",
        "label": "照护信息｜产后前两周支持情况",
        "type": "select",
        "required": True,
        "options": ["有人全天帮忙", "白天主要自己", "夜间主要自己", "支持少", "不确定"],
    },
    {
        "id": "top_worries",
        "label": "偏好信息｜最焦虑的事",
        "type": "multi_select",
        "required": True,
        "allow_other_input": True,
        "other_placeholder": "请简单写下你最担心的事",
        "options": [
            "不知道什么时候去医院",
            "怕漏买",
            "怕母乳不够",
            "怕剖宫产恢复",
            "怕产后没人帮",
            "怕宝宝用品准备不全",
            "其它",
        ],
    },
]
HOSPITAL_BAG_FORM_FIELD_IDS = {str(field["id"]) for field in HOSPITAL_BAG_FORM_FIELDS}
HOSPITAL_BAG_FORM_DETECTOR_FIELD_IDS = {
    "fetus_count",
    "return_to_work_timing",
    "top_worries",
}
BIRTH_PLAN_FORM_FIELDS = [
    {
        "id": "due_date_or_week",
        "label": "基本信息｜现在怀孕多久/预产期",
        "type": "text",
        "required": True,
        "placeholder": "例如：2026-06-12 或 37 周",
    },
    {
        "id": "birth_path",
        "label": "基本信息｜医生目前建议的生产方式",
        "type": "select",
        "required": True,
        "options": ["顺产", "剖宫产", "还没确定"],
    },
    {
        "id": "birth_setting",
        "label": "基本信息｜准备在哪家医院/哪里生",
        "type": "text",
        "required": False,
        "placeholder": "例如：某某医院、助产中心，或暂未确定",
    },
    {
        "id": "first_birth",
        "label": "基本信息｜是不是第一胎",
        "type": "select",
        "required": False,
        "options": ["是", "否", "还没确定"],
    },
    {
        "id": "top_priorities",
        "label": "支持与沟通｜最希望医护知道的事",
        "type": "multi_select",
        "required": True,
        "options": [
            "宝宝出生后，想尽早抱一抱/贴一贴",
            "想尽早试着喂母乳",
            "希望伴侣/支持人尽量陪在身边",
            "希望医护多鼓励我、告诉我进展",
            "一些非必要操作，希望先和我沟通",
        ],
    },
    {
        "id": "support_person",
        "label": "支持与沟通｜谁陪你、希望 TA 帮什么",
        "type": "text",
        "required": False,
        "placeholder": "例如：伴侣陪产并参与重要决定；妈妈在产后帮忙照顾",
    },
    {
        "id": "communication_preferences",
        "label": "支持与沟通｜希望医护怎么和你沟通",
        "type": "multi_select",
        "required": False,
        "options": [
            "做操作前，先告诉我为什么需要",
            "做重要决定前，先问问我的想法",
            "重要决定也请同步伴侣/支持人",
            "请用简单清楚的话说明",
            "计划有变化时，请先说原因和选择",
            "需要翻译或语言支持",
        ],
    },
    {
        "id": "priority_notes",
        "label": "支持与沟通｜还有什么想补充告诉医护",
        "type": "textarea",
        "required": False,
        "placeholder": "如果上面的选项没覆盖，可以简单写一句；不确定可留空。",
    },
    {
        "id": "labor_preferences",
        "label": "生产过程｜生宝宝时希望怎么被照顾",
        "type": "multi_select",
        "required": False,
        "options": [
            "医生允许时，希望可以走动或换姿势",
            "宝宝心跳监护怎么做，希望先说明一下",
            "希望可以用分娩球、热敷或按摩让自己舒服一点",
            "想提前确认生产时能不能喝水或吃点东西",
            "希望环境安静一点、灯光柔和一点",
        ],
    },
    {
        "id": "intervention_preferences",
        "label": "生产过程｜需要先说清楚的操作",
        "type": "multi_select",
        "required": False,
        "options": [
            "如果需要侧切，请先说明原因再和我沟通",
            "如果需要产钳或吸引，请先解释为什么需要",
            "如果需要人工破水，请先和我说明",
            "灌肠或剃毛前，希望先告诉我是否必须",
        ],
    },
    {
        "id": "pain_relief_preferences",
        "label": "疼痛和舒适｜生产时怎么帮你舒服一点",
        "type": "multi_select",
        "required": False,
        "options": [
            "想提前了解有哪些减痛/麻醉选择",
            "如果安全允许，先试试呼吸、姿势、按摩来缓解",
            "我倾向使用无痛/硬膜外，想提前沟通安排",
            "有点担心副作用或恢复，想先了解清楚再决定",
            "如果剖宫产，希望手术麻醉前充分说明",
        ],
    },
    {
        "id": "pain_relief_notes",
        "label": "疼痛和舒适｜其他关于疼痛缓解/麻醉的想法",
        "type": "textarea",
        "required": False,
        "placeholder": "如果上面的选项没覆盖，可以简单写一句；不确定可留空。",
    },
    {
        "id": "feeding_intention",
        "label": "宝宝出生后｜准备怎么喂宝宝",
        "type": "select",
        "required": False,
        "options": ["母乳喂养", "母乳和配方奶都可能", "配方奶", "还没想好"],
    },
    {
        "id": "baby_after_birth_preferences",
        "label": "宝宝出生后｜宝宝出生后希望怎么安排",
        "type": "multi_select",
        "required": False,
        "options": [
            "宝宝出生后，想尽早抱一抱/贴一贴",
            "想尽早试着亲喂/喂母乳",
            "如果医院允许，希望晚一点剪脐带",
            "希望宝宝尽量和我在一起",
            "给宝宝做检查或护理前，希望先告诉我",
            "打针、疫苗或新生儿检查前，希望先说明",
            "如果医院允许，希望伴侣/家人剪脐带",
            "如果医院允许，第一次洗澡晚一点",
            "如果宝宝需要离开我身边，请说明原因和大概多久",
            "给宝宝用配方奶或奶瓶前，请先和我沟通",
        ],
    },
    {
        "id": "if_plans_change",
        "label": "临时变化｜如果现场安排变了，希望怎么沟通",
        "type": "textarea",
        "required": False,
        "placeholder": "例如：请尽量解释原因；请让伴侣参与决定；请用简单语言说明选择。",
    },
    {
        "id": "emergency_authorization",
        "label": "临时变化｜如果来不及慢慢沟通，希望怎么处理",
        "type": "select",
        "required": False,
        "options": ["来不及细说时，优先按医生团队判断处理", "希望先联系我的伴侣/支持人", "希望尽量先直接告诉我", "还没确定"],
    },
    {
        "id": "hospital_questions_focus",
        "label": "提前问医院｜想提前问医院的问题",
        "type": "multi_select",
        "required": False,
        "options": [
            "陪产和探视怎么安排",
            "能不能拍照或录像",
            "生产时能不能喝水或吃点东西",
            "无痛或麻醉什么时候可以沟通",
            "宝宝出生后的护理流程",
            "产后有没有母乳喂养支持",
            "大概住几天、怎么出院",
            "紧急情况会怎么沟通和决定",
        ],
    },
    {
        "id": "medical_notes",
        "label": "提前问医院｜过敏、医生提醒或其他安全信息",
        "type": "textarea",
        "required": False,
        "placeholder": "只填写你明确知道的信息，例如：过敏、医生已说明的限制、医院要求。不确定可留空。",
    },
]
HOSPITAL_BAG_MISSING_LABELS = {
    "due_date_or_week": "预产期或当前孕周",
    "first_birth": "是否第一胎",
    "fetus_count": "本次妊娠胎数",
    "pregnancy_history_or_notes": "医生是否提示过特殊情况",
    "birth_path": "分娩方式",
    "feeding_intention": "喂养意向",
    "return_to_work_timing": "产后多久返工",
    "support_person": "产后前两周支持情况",
    "top_worries": "最焦虑的事",
}
HOSPITAL_BAG_FIELD_LABELS = {
    "due_date_or_week": "孕周/预产期",
    "first_birth": "是否第一胎",
    "fetus_count": "胎数",
    "pregnancy_history_or_notes": "医生提示",
    "birth_path": "分娩方式",
    "feeding_intention": "喂养意向",
    "return_to_work_timing": "返工时间",
    "support_person": "支持情况",
    "top_worries": "焦虑点",
}
HOSPITAL_BAG_REASON_SUPPRESSED_ITEM_LABELS = {
    "检查报告/化验单",
    "医院预登记信息",
    "紧急联系人信息",
    "医生/医院联系电话",
    "手机充电线和充电器",
    "医院路线和停车信息",
    "夜间入口信息",
}
HOSPITAL_BAG_REASON_SUPPRESSED_GROUP_IDS = {"support_person_bag"}
HOSPITAL_BAG_DISCLAIMER = "请优先遵循医院要求和医生/助产士的具体指导。"
HOSPITAL_BAG_PROVIDED_ITEM_ALIASES = {
    "尿布": ("纸尿裤",),
    "宝宝衣服": ("宝宝出院衣物",),
    "宝宝衣物": ("宝宝出院衣物",),
    "新生儿衣服": ("宝宝出院衣物",),
    "新生儿衣物": ("宝宝出院衣物",),
    "卫生巾": ("产褥垫/产妇卫生巾",),
    "产妇卫生巾": ("产褥垫/产妇卫生巾",),
    "产褥垫": ("产褥垫/产妇卫生巾", "备用产褥垫/卫生巾"),
    "湿巾": ("湿巾/棉柔巾", "纸巾/湿巾"),
    "棉柔巾": ("湿巾/棉柔巾",),
}


def create_form(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    form_id = str(args.get("form_id", "form"))
    fields = _normalize_form_fields(args.get("fields", []))
    description = str(args.get("description", ""))
    hospital_bag_default_values: dict[str, Any] | None = None
    if _looks_like_hospital_bag_form(form_id, fields):
        form_id = "hospital_bag_intake"
        state_default_values = _dict_value(inputs.get("_birth_prep_hospital_bag_slots"))
        field_default_values = _default_values_from_form_fields(fields)
        arg_default_values = _dict_value(args.get("default_values"))
        default_values = {
            **_birth_prep_shared_default_values(inputs),
            **_birth_prep_shared_values_from_source(state_default_values),
            **state_default_values,
            **_birth_prep_shared_values_from_source(field_default_values),
            **field_default_values,
            **_birth_prep_shared_values_from_source(arg_default_values),
            **arg_default_values,
        }
        hospital_bag_default_values = _hospital_bag_allowed_default_values(default_values)
        fields = _hospital_bag_fields_with_defaults(hospital_bag_default_values)
        description = ""
    elif form_id == "birth_plan_card_intake":
        fields = [_sanitize_birth_plan_form_field(_without_field_help_text(field)) for field in fields]
        description = ""
    form = {
        "id": form_id,
        "title": args.get("title", ""),
        "description": description,
        "submit_label": args.get("submit_label", "确认"),
        "fields": fields,
    }
    if hospital_bag_default_values is not None:
        form["default_values"] = hospital_bag_default_values
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": form,
    }


def create_hospital_bag_form(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    profile_default_values = _birth_prep_shared_default_values(inputs)
    state_default_values = _dict_value(inputs.get("_birth_prep_hospital_bag_slots"))
    arg_default_values = _dict_value(args.get("default_values"))
    default_values = {
        **profile_default_values,
        **_birth_prep_shared_values_from_source(state_default_values),
        **state_default_values,
        **_birth_prep_shared_values_from_source(arg_default_values),
        **arg_default_values,
    }
    form_default_values = _hospital_bag_allowed_default_values(default_values)
    fields = _hospital_bag_fields_with_defaults(form_default_values)
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": "hospital_bag_intake",
            "title": "信息采集",
            "description": "",
            "submit_label": "提交",
            "fields": fields,
            "default_values": form_default_values,
        },
    }


def _hospital_bag_fields_with_defaults(form_default_values: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for template in HOSPITAL_BAG_FORM_FIELDS:
        field = dict(template)
        value = _first_text(form_default_values.get(field["id"]))
        is_unknown_birth_path = field["id"] == "birth_path" and _normalize_hospital_bag_birth_path(value) == "还不确定"
        if value and (_normalized_placeholder(value) not in PLACEHOLDER_VALUES or is_unknown_birth_path):
            normalized_value = _normalize_hospital_bag_default_value(field["id"], value)
            if normalized_value is not None:
                field["default_value"] = normalized_value
        fields.append(field)
    return fields


def _hospital_bag_allowed_default_values(default_values: dict[str, Any]) -> dict[str, Any]:
    normalized_default_values = _normalize_hospital_bag_default_values(default_values)
    return {
        field_id: normalized_default_values[field_id]
        for field_id in HOSPITAL_BAG_FORM_FIELD_IDS
        if field_id in normalized_default_values
    }


def _needs_context_result(
    tool_name: str,
    status: str,
    summary: str,
    missing_fields: list[str],
    confirmation_question: str,
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "summary": summary,
        "missing_fields": missing_fields,
        "data": {
            "confirmation_question": confirmation_question,
        },
    }


def _missing_hospital_bag_required_form_fields(form_data: dict[str, Any]) -> list[str]:
    return [
        field_id
        for field_id in HOSPITAL_BAG_MISSING_LABELS
        if not _has_confirmed_hospital_bag_form_value(field_id, form_data.get(field_id))
    ]


def _hospital_bag_required_form_question(missing_fields: list[str]) -> str:
    if not missing_fields:
        return ""
    labels = [HOSPITAL_BAG_MISSING_LABELS.get(field_id, field_id) for field_id in missing_fields[:3]]
    return f"待产包清单还不能生成，表单里还差{'、'.join(labels)}。请先补全并提交待产包信息采集表单。"


def _has_confirmed_form_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_confirmed_form_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_confirmed_form_value(item) for item in value.values())
    text = _first_text(value)
    return _has_meaningful_value(text) and _normalized_placeholder(text) not in PLACEHOLDER_VALUES


def _has_confirmed_hospital_bag_form_value(field_id: str, value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_confirmed_hospital_bag_form_value(field_id, item) for item in value)
    if isinstance(value, dict):
        return any(_has_confirmed_hospital_bag_form_value(field_id, item) for item in value.values())
    text = _first_nonempty_text(value)
    if not text:
        return False
    normalized = _normalize_hospital_bag_form_value(field_id, text)
    if normalized in _hospital_bag_field_options(field_id):
        return True
    return _has_confirmed_form_value(text)


def _default_values_from_form_fields(fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        str(field.get("id") or ""): field.get("default_value")
        for field in fields
        if isinstance(field, dict) and field.get("id") and field.get("default_value") is not None
    }


def _looks_like_hospital_bag_form(form_id: str, fields: list[dict[str, Any]]) -> bool:
    if form_id == "hospital_bag_intake":
        return True
    field_ids = {str(field.get("id") or "") for field in fields if isinstance(field, dict)}
    return bool(field_ids & HOSPITAL_BAG_FORM_DETECTOR_FIELD_IDS) and len(field_ids & HOSPITAL_BAG_FORM_FIELD_IDS) >= 2


def create_hospital_bag_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    form_data = _confirmed_form_data_for_form(inputs, "hospital_bag_intake")
    if not form_data:
        return _needs_context_result(
            "hospital_bag_card_create",
            "needs_confirmed_form_data",
            "生成待产包清单前，需要先提交待产包信息采集表单。",
            list(HOSPITAL_BAG_MISSING_LABELS),
            "请先完成并提交待产包信息采集表单，我再根据确认后的信息整理待产包清单。",
        )
    missing_form_fields = _missing_hospital_bag_required_form_fields(form_data)
    if missing_form_fields:
        return _needs_context_result(
            "hospital_bag_card_create",
            "needs_required_form_fields",
            "生成待产包清单前，需要先补全待产包表单必填信息。",
            missing_form_fields,
            _hospital_bag_required_form_question(missing_form_fields),
        )
    generation_mode = str(args.get("generation_mode") or "standard")
    _persist_birth_prep_profile_memory(inputs, form_data)
    card_json = _build_hospital_bag_card_json(form_data, generation_mode, inputs)
    _normalize_hospital_bag_scene_groups(card_json["packing_groups"])
    _suppress_hospital_bag_personalization(card_json["packing_groups"])
    _apply_hospital_bag_item_explanations(card_json["packing_groups"])
    return {
        "tool_name": "hospital_bag_card_create",
        "status": "card_created",
        "card": {
            "card_type": "hospital_bag_card",
            "schema_version": "1.0",
            "card_json": card_json,
        },
        "assistant_followup": _hospital_bag_cart_followup(card_json),
    }


def recommend_hospital_bag_pump(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    use_case = str(args.get("use_case") or "unknown").strip() or "unknown"
    preference = str(args.get("preference") or "balanced").strip() or "balanced"
    feeding_intention = str(args.get("feeding_intention") or "unknown").strip() or "unknown"
    requested_model = str(args.get("requested_model") or "").strip()
    target_budget_usd = _target_budget(args.get("target_budget_usd"))
    must_have_app = args.get("must_have_app")
    need_single_unit = args.get("need_single_unit")
    ranked = _rank_momcozy_pump_products(
        use_case=use_case,
        preference=preference,
        target_budget_usd=target_budget_usd,
        must_have_app=must_have_app if isinstance(must_have_app, bool) else None,
        need_single_unit=need_single_unit if isinstance(need_single_unit, bool) else None,
    )
    requested_product = _momcozy_pump_product(requested_model) if requested_model else None
    if not ranked:
        message = "我先确认一下：你更在意预算、通勤隐蔽，还是吸奶效率？这样我再帮你选型号会更准。"
        return {
            "tool_name": "hospital_bag_pump_recommend",
            "status": "needs_clarification",
            "summary": message,
            "message": message,
            "recommended_product": None,
            "alternatives": [],
            "cart_sync_suggestion": None,
        }

    recommended = requested_product or ranked[0]
    alternatives = [product for product in ranked if product["sku_id"] != recommended["sku_id"]][:2]
    product = _public_pump_product(recommended)
    summary = _pump_recommendation_message(
        recommended,
        alternatives,
        use_case=use_case,
        preference=preference,
        feeding_intention=feeding_intention,
        target_budget_usd=target_budget_usd,
        requested_model=requested_model if requested_product else None,
    )
    return {
        "tool_name": "hospital_bag_pump_recommend",
        "status": "pump_recommended",
        "recommendation_mode": "requested_model_review" if requested_product else "ranked_recommendation",
        "summary": summary,
        "message": summary,
        "recommended_product": product,
        "alternatives": [_public_pump_product(product) for product in alternatives],
        "price_guidance": _pump_price_guidance(recommended, alternatives),
        "cart_sync_suggestion": {
            "tool_name": "hospital_bag_cart_update",
            "action": "replace_pump_model",
            "product_sku_id": recommended["sku_id"],
            "item_ids": [HOSPITAL_BAG_CART_PUMP_ITEM_ID],
        },
        "source_urls": [
            MOMCOZY_PUMP_OFFICIAL_OVERVIEW_URL,
            MOMCOZY_PUMP_OFFICIAL_COLLECTION_URL,
            MOMCOZY_PUMP_SUPPORT_GUIDE_URL,
        ],
    }


def update_hospital_bag_cart(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    assistant_message = str(args.get("assistant_message") or "").strip()
    item_ids = [
        str(item_id).strip()
        for item_id in args.get("item_ids", [])
        if str(item_id).strip()
    ]
    preserve_item_ids = [
        str(item_id).strip()
        for item_id in args.get("preserve_item_ids", [])
        if str(item_id).strip()
    ]
    current_groups = _hospital_bag_cart_groups_from_inputs(inputs)

    if action in {"replace_pump_model", "add_pump_model"}:
        product_sku_id = _first_text(args.get("product_sku_id"))
        product = _momcozy_pump_product(product_sku_id)
        if not product:
            message = assistant_message or "你想换成哪一款 Momcozy 吸奶器？比如 S12 Pro Quick、M5 Smart、M9。"
            return {
                "tool_name": "hospital_bag_cart_update",
                "status": "needs_clarification",
                "summary": message,
                "cart_update": {"action": "clarify", "message": message},
            }
        next_groups, changed = _upsert_hospital_bag_pump_model(current_groups, product)
        totals = _hospital_bag_cart_totals(next_groups)
        if changed["mode"] == "unchanged":
            message = assistant_message or f"购物车里已经是「{product['name']}」了，我先不重复添加。"
        elif changed["mode"] == "added":
            message = assistant_message or f"好，我把「{product['name']}」加到母乳喂养里了，官方价折合约 {_pump_price_cny_label(product)}。"
        else:
            from_name = changed.get("from_name") or "原来的吸奶器"
            message = assistant_message or f"好，我把「{from_name}」换成「{product['name']}」了，官方价折合约 {_pump_price_cny_label(product)}。"
        return _hospital_bag_cart_update_result(
            action,
            next_groups,
            totals,
            message,
            replaced_items=[changed] if changed["mode"] == "replaced" else [],
            restored_item_ids=[product["sku_id"]] if changed["mode"] == "added" else [],
            restored_item_names=[product["name"]] if changed["mode"] == "added" else [],
        )

    if action in {"optimize_budget", "apply_budget_plan"}:
        budget_result = _optimize_hospital_bag_cart_budget(
            current_groups,
            target_budget=_target_budget(args.get("target_budget")),
            budget_mode=str(args.get("budget_mode") or "cheaper"),
            preference=str(args.get("preference") or "balanced"),
            preserve_item_ids=preserve_item_ids,
            allow_remove_pump=bool(args.get("allow_remove_pump")),
        )
        message = assistant_message or _hospital_bag_budget_message(budget_result)
        return _hospital_bag_cart_update_result(
            "optimize_budget",
            budget_result["groups"],
            budget_result["totals"],
            message,
            before_totals=budget_result["before_totals"],
            removed_item_ids=budget_result["removed_item_ids"],
            removed_item_names=budget_result["removed_item_names"],
            replaced_items=budget_result["replaced_items"],
            target_budget=budget_result["target_budget"],
            budget_met=budget_result["budget_met"],
        )

    if action in {"remove_items", "mark_provided", "mark_owned"}:
        if not item_ids:
            message = assistant_message or _missing_item_message(action)
            return {
                "tool_name": "hospital_bag_cart_update",
                "status": "needs_clarification",
                "summary": message,
                "cart_update": {"action": "clarify", "message": message},
            }
        next_groups, removed_names = _remove_hospital_bag_cart_items(current_groups, item_ids)
        if not removed_names:
            message = assistant_message or "我没有在当前购物车里找到这件商品，你可以再说一下商品名。"
            return {
                "tool_name": "hospital_bag_cart_update",
                "status": "cart_unchanged",
                "summary": message,
                "cart_update": {"action": "clarify", "message": message},
            }
        totals = _hospital_bag_cart_totals(next_groups)
        names = "、".join(f"「{name}」" for name in removed_names)
        message = assistant_message or _remove_items_message(action, names, totals)
        return _hospital_bag_cart_update_result(
            action,
            next_groups,
            totals,
            message,
            removed_item_ids=item_ids,
            removed_item_names=removed_names,
        )

    if action == "restore_items":
        if not item_ids:
            message = assistant_message or "你想加回哪一件？直接告诉我商品名就行。"
            return {
                "tool_name": "hospital_bag_cart_update",
                "status": "needs_clarification",
                "summary": message,
                "cart_update": {"action": "clarify", "message": message},
            }
        next_groups, restored_names = _restore_hospital_bag_cart_items(current_groups, item_ids)
        totals = _hospital_bag_cart_totals(next_groups)
        if not restored_names:
            message = assistant_message or "这些商品已经在购物车里了，不需要重复添加。"
        else:
            names = "、".join(f"「{name}」" for name in restored_names)
            message = assistant_message or f"好，我把{names}加回购物车了，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_result(
            action,
            next_groups,
            totals,
            message,
            restored_item_ids=item_ids,
            restored_item_names=restored_names,
        )

    if action == "replace_items":
        next_groups, replaced_items = _replace_hospital_bag_cart_items(current_groups, item_ids)
        totals = _hospital_bag_cart_totals(next_groups)
        if not replaced_items:
            message = assistant_message or "当前购物车里暂时没有可替换成基础款的商品。"
        else:
            names = "、".join(f"「{item['from_name']}」换成「{item['to_name']}」" for item in replaced_items)
            message = assistant_message or f"可以，我先帮你把{names}，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_result(
            action,
            next_groups,
            totals,
            message,
            replaced_items=replaced_items,
        )

    if action == "update_quantity":
        quantity_updates = args.get("quantity_updates")
        if not isinstance(quantity_updates, list) or not quantity_updates:
            message = assistant_message or "你想把哪件商品改成几件？直接告诉我商品名和数量就行。"
            return {
                "tool_name": "hospital_bag_cart_update",
                "status": "needs_clarification",
                "summary": message,
                "cart_update": {"action": "clarify", "message": message},
            }
        next_groups, updated_names, removed_names = _update_hospital_bag_cart_quantities(current_groups, quantity_updates)
        totals = _hospital_bag_cart_totals(next_groups)
        if not updated_names and not removed_names:
            message = assistant_message or "我没有在当前购物车里找到要调整的商品，你可以再说一下商品名。"
        else:
            parts = []
            if updated_names:
                parts.append("调整了" + "、".join(f"「{name}」" for name in updated_names))
            if removed_names:
                parts.append("移除了" + "、".join(f"「{name}」" for name in removed_names))
            parts_text = "，".join(parts)
            message = assistant_message or f"好，我已经{parts_text}，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_result(
            action,
            next_groups,
            totals,
            message,
            removed_item_names=removed_names,
        )

    if action == "reset_cart":
        next_groups = _clone_hospital_bag_cart_groups(DEFAULT_HOSPITAL_BAG_CART_GROUPS)
        totals = _hospital_bag_cart_totals(next_groups)
        message = assistant_message or f"已经帮你把待产包购物车恢复到默认清单了，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_result(action, next_groups, totals, message)

    if action == "clarify":
        message = assistant_message or "你想怎么调整购物车？比如删掉某件、换便宜一点，或者恢复默认清单。"
        return {
            "tool_name": "hospital_bag_cart_update",
            "status": "needs_clarification",
            "summary": message,
            "cart_update": {"action": "clarify", "message": message},
        }

    raise ValueError(f"unsupported hospital bag cart action: {action}")


def _rank_momcozy_pump_products(
    *,
    use_case: str,
    preference: str,
    target_budget_usd: float | None,
    must_have_app: bool | None,
    need_single_unit: bool | None,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for product in MOMCOZY_PUMP_PRODUCT_CATALOG:
        score = 0.0
        product_use_cases = set(product.get("use_cases") or [])
        product_preferences = set(product.get("preferences") or [])
        price = float(product["price_usd"])

        if use_case in product_use_cases:
            score += 5
        if preference in product_preferences:
            score += 4
        if preference == "budget":
            score += max(0, 4 - price / 100)
        if preference == "balanced" and product.get("tier") in {"entry_plus", "mid", "mid_plus", "pro"}:
            score += 2
        if preference == "performance" and product.get("suction") == "最高 -300 mmHg":
            score += 2
        if must_have_app is not None:
            score += 4 if bool(product.get("app")) == must_have_app else -5
        if need_single_unit is not None:
            score += 3 if bool(product.get("supports_single_unit")) == need_single_unit else -3
        if target_budget_usd is not None:
            if price <= target_budget_usd:
                score += 5
            else:
                score -= min(8, (price - target_budget_usd) / 25)
        score -= price / 1000
        scored.append((score, product))
    scored.sort(key=lambda item: (-item[0], float(item[1]["price_usd"])))
    return [product for _, product in scored]


def _pump_recommendation_message(
    product: dict[str, Any],
    alternatives: list[dict[str, Any]],
    *,
    use_case: str,
    preference: str,
    feeding_intention: str,
    target_budget_usd: float | None,
    requested_model: str | None = None,
) -> str:
    reason_parts = [str(product.get("best_for") or "").strip()]
    feature_text = "、".join(str(feature) for feature in product.get("features", [])[:3])
    if feature_text:
        reason_parts.append(f"重点是{feature_text}")
    if target_budget_usd is not None and float(product["price_usd"]) > target_budget_usd:
        reason_parts.append(f"不过它的官方价折合约 {_pump_price_cny_label(product)}，会超过你说的约 {_money_label(_usd_to_cny(target_budget_usd), 'CNY')} 预算")
    elif target_budget_usd is not None:
        reason_parts.append(f"官方价折合约 {_pump_price_cny_label(product)}，在你说的约 {_money_label(_usd_to_cny(target_budget_usd), 'CNY')} 预算内")
    else:
        reason_parts.append(f"官方价折合约 {_pump_price_cny_label(product)}")

    prefix = "我会优先看你当前的使用场景和预算，不硬推最贵款。"
    if feeding_intention == "formula":
        prefix = "如果你只是少量备用，我会先按轻量备用来选，不建议直接上很贵的型号。"
    elif use_case in {"work_pumping", "portable"}:
        prefix = "你这个场景更看重外出时好带、好操作，我会优先选便携和控制体验。"
    elif preference == "budget":
        prefix = "你更在意预算的话，我会先选入门里更稳的一款。"

    alternative_text = ""
    if alternatives:
        names = "、".join(f"{item['model']}（约 {_pump_price_cny_label(item)}）" for item in alternatives)
        alternative_text = f"备选可以看 {names}。"

    if requested_model:
        prefix = f"「{product['model']}」可以考虑，我先按它的官方价格和适用场景评估。"
        if product["sku_id"] == "pump-air-1":
            prefix = "「Air 1」可以考虑，但它是高价轻薄/隐蔽升级款，不是降低预算选择。"
        return f"{prefix}\n\n{'；'.join(part for part in reason_parts if part)}。{alternative_text}"

    return f"{prefix}\n\n我建议先选「{product['model']}」。{'；'.join(part for part in reason_parts if part)}。{alternative_text}"


def _pump_price_guidance(product: dict[str, Any], alternatives: list[dict[str, Any]]) -> str:
    products = [product, *alternatives]
    if any(str(item.get("sku_id") or "") == "pump-air-1" for item in products):
        air = _momcozy_pump_product("pump-air-1") or product
        return (
            f"Air 1 是高价轻薄款，官方价折合约 {_pump_price_cny_label(air)}；"
            "不能把 Air 1 描述为降低预算或省钱选择。"
            "若用户要省预算，应优先说明 S9 Pro、S12 Pro Quick 等更低价型号。"
        )
    return "价格比较必须按 official_price_usd / sale_price_usd 和 price_label / sale_price_label 数值说明；不要把更高价型号描述为省预算。"


def _pump_price_position(product: dict[str, Any]) -> str:
    sku_id = str(product.get("sku_id") or "")
    price = float(product.get("price_usd") or 0)
    if sku_id == "pump-air-1":
        return "premium_highest"
    if price >= 300:
        return "premium_high"
    if price >= 150:
        return "upper_mid"
    if price >= 100:
        return "mid"
    return "budget"


def _pump_budget_note(product: dict[str, Any]) -> str:
    sku_id = str(product.get("sku_id") or "")
    if sku_id == "pump-air-1":
        return "Air 1 是高价轻薄/隐蔽升级选择，不适合描述为降低预算；预算优先时应看 S9 Pro 或 S12 Pro Quick。"
    if sku_id in {"pump-s9-pro", "pump-s12-pro-quick"}:
        return "预算优先时更适合优先考虑。"
    return "按功能、舒适度和预算综合比较。"


def _public_pump_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku_id": product["sku_id"],
        "model": product["model"],
        "name": product["name"],
        "image_url": MOMCOZY_PUMP_IMAGE_URLS.get(str(product["sku_id"]) or ""),
        "image_alt": product["name"],
        "official_price_usd": product["price_usd"],
        "sale_price_usd": product.get("sale_price_usd"),
        "price_cny": _usd_to_cny(product["price_usd"]),
        "sale_price_cny": _usd_to_cny(product.get("sale_price_usd") or product["price_usd"]),
        "price_label": _pump_price_cny_label(product),
        "sale_price_label": _pump_sale_price_cny_label(product),
        "exchange_rate_usd_cny": HOSPITAL_BAG_CART_USD_TO_CNY_RATE,
        "price_position": _pump_price_position(product),
        "budget_note": _pump_budget_note(product),
        "best_for": product.get("best_for"),
        "features": list(product.get("features") or []),
        "suction": product.get("suction"),
        "battery": product.get("battery"),
        "weight": product.get("weight"),
        "noise": product.get("noise"),
        "app": bool(product.get("app")),
        "supports_single_unit": bool(product.get("supports_single_unit")),
        "source_url": MOMCOZY_PUMP_OFFICIAL_COLLECTION_URL,
    }


def _momcozy_pump_product(sku_id_or_model: str) -> dict[str, Any] | None:
    token = _item_match_key(sku_id_or_model).lower()
    if not token:
        return None
    for product in MOMCOZY_PUMP_PRODUCT_CATALOG:
        aliases = [
            product["sku_id"],
            product["model"],
            product["name"],
            str(product["model"]).replace(" ", ""),
        ]
        if token in {_item_match_key(alias).lower() for alias in aliases}:
            return product
    return None


def _hospital_bag_pump_item_ids() -> set[str]:
    return {HOSPITAL_BAG_CART_PUMP_ITEM_ID, *(str(product["sku_id"]) for product in MOMCOZY_PUMP_PRODUCT_CATALOG)}


def _hospital_bag_cart_pump_item(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": product["sku_id"],
        "sku_id": product["sku_id"],
        "model": product["model"],
        "name": product["name"],
        "desc": _pump_cart_description(product),
        "qty": 1,
        "price": _usd_to_cny(product["price_usd"]),
        "currency": "CNY",
        "price_label": _pump_price_cny_label(product),
        "sale_price_label": _pump_sale_price_cny_label(product),
        "official_price_usd": float(product["price_usd"]),
        "sale_price_usd": float(product.get("sale_price_usd") or product["price_usd"]),
        "exchange_rate_usd_cny": HOSPITAL_BAG_CART_USD_TO_CNY_RATE,
        "product_url": MOMCOZY_PUMP_OFFICIAL_COLLECTION_URL,
        "image_url": MOMCOZY_PUMP_IMAGE_URLS.get(str(product["sku_id"]) or ""),
        "image_alt": product["name"],
        "keywords": ["吸奶器", "Momcozy", str(product["model"]), str(product["sku_id"])],
    }


def _pump_cart_description(product: dict[str, Any]) -> str:
    features = "、".join(str(feature) for feature in product.get("features", [])[:2])
    if features:
        return f"{features}；{product.get('best_for') or ''}".strip("；")
    return str(product.get("best_for") or "")


def _pump_price_label(product: dict[str, Any]) -> str:
    return f"${float(product['price_usd']):.2f} USD"


def _pump_sale_price_label(product: dict[str, Any]) -> str:
    sale_price = product.get("sale_price_usd")
    if not isinstance(sale_price, (int, float)):
        return ""
    prefix = "From " if product.get("supports_single_unit") else ""
    return f"{prefix}${float(sale_price):.2f} USD"


def _pump_price_cny_label(product: dict[str, Any]) -> str:
    return _money_label(_usd_to_cny(product["price_usd"]), "CNY")


def _pump_sale_price_cny_label(product: dict[str, Any]) -> str:
    sale_price = product.get("sale_price_usd")
    if not isinstance(sale_price, (int, float)):
        return ""
    prefix = "约 " if product.get("supports_single_unit") else ""
    return f"{prefix}{_money_label(_usd_to_cny(sale_price), 'CNY')}"


def _usd_to_cny(value: Any) -> float:
    return round(_cart_number(value, default=0) * HOSPITAL_BAG_CART_USD_TO_CNY_RATE, 2)


def _upsert_hospital_bag_pump_model(
    groups: list[dict[str, Any]],
    product: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    next_groups = _clone_hospital_bag_cart_groups(groups)
    pump_ids = _hospital_bag_pump_item_ids()
    next_item = _hospital_bag_cart_pump_item(product)
    for group in next_groups:
        items = group.get("items", [])
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if item_id == next_item["id"]:
                return next_groups, {"mode": "unchanged", "item_id": next_item["id"], "name": next_item["name"]}
            if item_id in pump_ids or "吸奶器" in str(item.get("name") or ""):
                from_name = _first_text(item.get("name"))
                items[index] = next_item
                return next_groups, {
                    "mode": "replaced",
                    "from_item_id": item_id,
                    "from_name": from_name,
                    "to_item_id": next_item["id"],
                    "to_name": next_item["name"],
                }
    group = _find_or_create_hospital_bag_cart_group(next_groups, "母乳喂养", "sky")
    group["items"].append(next_item)
    return next_groups, {"mode": "added", "item_id": next_item["id"], "name": next_item["name"]}


def _hospital_bag_cart_update_result(
    action: str,
    groups: list[dict[str, Any]],
    totals: dict[str, Any],
    message: str,
    *,
    before_totals: dict[str, Any] | None = None,
    removed_item_ids: list[str] | None = None,
    removed_item_names: list[str] | None = None,
    restored_item_ids: list[str] | None = None,
    restored_item_names: list[str] | None = None,
    replaced_items: list[dict[str, Any]] | None = None,
    target_budget: float | None = None,
    budget_met: bool | None = None,
) -> dict[str, Any]:
    cart_update: dict[str, Any] = {
        "action": action,
        "groups": groups,
        "totals": totals,
        "removed_item_ids": removed_item_ids or [],
        "removed_item_names": removed_item_names or [],
        "restored_item_ids": restored_item_ids or [],
        "restored_item_names": restored_item_names or [],
        "replaced_items": replaced_items or [],
        "message": message,
    }
    if before_totals is not None:
        cart_update["before_totals"] = before_totals
    if target_budget is not None:
        cart_update["target_budget"] = target_budget
    if budget_met is not None:
        cart_update["budget_met"] = budget_met
    return {
        "tool_name": "hospital_bag_cart_update",
        "status": "cart_updated",
        "summary": message,
        "cart_update": cart_update,
    }


def _hospital_bag_cart_groups_from_inputs(inputs: RuntimeInputs) -> list[dict[str, Any]]:
    cart = inputs.get("hospital_bag_cart")
    if isinstance(cart, dict):
        groups = cart.get("groups")
        if isinstance(groups, list):
            sanitized = _sanitize_hospital_bag_cart_groups(groups)
            if sanitized:
                return sanitized
    return _clone_hospital_bag_cart_groups(DEFAULT_HOSPITAL_BAG_CART_GROUPS)


def _sanitize_hospital_bag_cart_groups(groups: list[Any]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        title = _first_text(group.get("title")) or "待产包"
        tone = _first_text(group.get("tone")) or "rose"
        if tone not in {"rose", "mint", "sky"}:
            tone = "rose"
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = _first_text(item.get("id"))
            name = _first_text(item.get("name"))
            if not item_id or not name:
                continue
            qty = _cart_number(item.get("qty"), default=1)
            price = _cart_number(item.get("price"), default=0)
            keywords = item.get("keywords")
            next_item = {
                "id": item_id,
                "name": name,
                "desc": _first_text(item.get("desc")),
                "qty": max(1, int(qty)),
                "price": round(price, 2),
                "keywords": [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
                if isinstance(keywords, list)
                else [],
            }
            _copy_optional_cart_item_fields(item, next_item)
            _apply_default_cart_item_image(next_item)
            items.append(next_item)
        sanitized.append({"title": title, "tone": tone, "items": items})
    return sanitized


def _clone_hospital_bag_cart_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": str(group.get("title") or ""),
            "tone": str(group.get("tone") or "rose"),
            "items": [
                _clone_hospital_bag_cart_item(item)
                for item in group.get("items", [])
                if isinstance(item, dict)
            ],
        }
        for group in groups
        if isinstance(group, dict)
    ]


def _clone_hospital_bag_cart_item(item: dict[str, Any]) -> dict[str, Any]:
    cloned = {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or ""),
        "desc": str(item.get("desc") or ""),
        "qty": int(item.get("qty") or 1),
        "price": float(item.get("price") or 0),
        "keywords": list(item.get("keywords") or []),
    }
    _copy_optional_cart_item_fields(item, cloned)
    _apply_default_cart_item_image(cloned)
    return cloned


def _copy_optional_cart_item_fields(source: dict[str, Any], target: dict[str, Any]) -> None:
    for key in (
        "currency",
        "price_label",
        "sale_price_label",
        "official_price_usd",
        "sale_price_usd",
        "exchange_rate_usd_cny",
        "product_url",
        "image_url",
        "image_alt",
        "sku_id",
        "model",
    ):
        if key in source and source[key] is not None:
            target[key] = source[key]


def _apply_default_cart_item_image(item: dict[str, Any]) -> None:
    if item.get("image_url"):
        return
    item_id = str(item.get("id") or "")
    image_url = MOMCOZY_PUMP_IMAGE_URLS.get(item_id) or HOSPITAL_BAG_CART_PRODUCT_IMAGE_URLS.get(item_id)
    if not image_url:
        return
    item["image_url"] = image_url
    if not item.get("image_alt"):
        item["image_alt"] = str(item.get("name") or "商品图")


def _hospital_bag_cart_totals(groups: list[dict[str, Any]]) -> dict[str, Any]:
    subtotal = 0.0
    item_count = 0
    converted_usd_subtotal = 0.0
    for group in groups:
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            qty = max(1, int(_cart_number(item.get("qty"), default=1)))
            line_total = _cart_item_line_total_cny(item, qty)
            subtotal = round(subtotal + line_total, 2)
            item_count += qty
            if _cart_item_currency(item) == "USD":
                converted_usd_subtotal = round(converted_usd_subtotal + line_total, 2)
    discount = round(subtotal * 0.08, 2) if item_count > 0 else 0
    shipping = 0
    total = round(subtotal - discount + shipping, 2)
    currency_totals = [
        {
            "currency": "CNY",
            "subtotal": subtotal,
            "discount": discount,
            "shipping": shipping,
            "total": total,
            "itemCount": item_count,
        }
    ]
    return {
        "subtotal": subtotal,
        "itemCount": item_count,
        "discount": discount,
        "shipping": shipping,
        "total": total,
        "currency_totals": currency_totals,
        "mixed_currency": False,
        "exchange_rate_usd_cny": HOSPITAL_BAG_CART_USD_TO_CNY_RATE,
        "converted_usd_subtotal": converted_usd_subtotal,
    }


def _cart_item_line_total_cny(item: dict[str, Any], qty: int) -> float:
    price = _cart_number(item.get("price"), default=0)
    if _cart_item_currency(item) == "USD":
        price = _usd_to_cny(price)
    return round(price * qty, 2)


def _cart_item_currency(item: dict[str, Any]) -> str:
    currency = str(item.get("currency") or "CNY").strip().upper()
    if currency in {"USD", "CNY"}:
        return currency
    return "CNY"


def _cart_totals_label(totals: dict[str, Any]) -> str:
    return _money_label(totals.get("total"), "CNY")


def _money_label(value: Any, currency: str) -> str:
    amount = _cart_number(value, default=0)
    if currency.upper() == "USD":
        return f"${amount:.2f}"
    return f"¥{amount:.2f}"


def _optimize_hospital_bag_cart_budget(
    groups: list[dict[str, Any]],
    *,
    target_budget: float | None,
    budget_mode: str,
    preference: str,
    preserve_item_ids: list[str],
    allow_remove_pump: bool,
) -> dict[str, Any]:
    before_totals = _hospital_bag_cart_totals(groups)
    next_groups = _clone_hospital_bag_cart_groups(groups)
    protected_ids = set(HOSPITAL_BAG_CART_PROTECTED_ITEM_IDS)
    protected_ids.update(preserve_item_ids)
    if not allow_remove_pump:
        protected_ids.update(_hospital_bag_pump_item_ids())

    replaced_items: list[dict[str, Any]] = []
    if preference != "comfort":
        next_groups, replaced_items = _replace_hospital_bag_cart_items(next_groups, ["baby-blanket"])

    removed_ids: list[str] = []
    removed_names: list[str] = []
    if target_budget is None:
        candidate_ids = HOSPITAL_BAG_CART_LIGHT_BUDGET_REMOVE_IDS if budget_mode != "minimal" else set(HOSPITAL_BAG_CART_BUDGET_REMOVE_ORDER)
        next_groups, removed_names = _remove_hospital_bag_cart_items(
            next_groups,
            [item_id for item_id in HOSPITAL_BAG_CART_BUDGET_REMOVE_ORDER if item_id in candidate_ids and item_id not in protected_ids],
        )
        removed_ids = _ids_for_names(groups, removed_names)
    else:
        for item_id in _budget_removal_order(allow_remove_pump=allow_remove_pump, preference=preference):
            if item_id in protected_ids:
                continue
            totals = _hospital_bag_cart_totals(next_groups)
            if totals["total"] <= target_budget:
                break
            next_groups_candidate, names = _remove_hospital_bag_cart_items(next_groups, [item_id])
            if not names:
                continue
            next_groups = next_groups_candidate
            removed_ids.append(item_id)
            removed_names.extend(names)

    totals = _hospital_bag_cart_totals(next_groups)
    budget_met = target_budget is None or totals["total"] <= target_budget
    return {
        "groups": next_groups,
        "before_totals": before_totals,
        "totals": totals,
        "target_budget": target_budget,
        "budget_met": budget_met,
        "removed_item_ids": removed_ids,
        "removed_item_names": removed_names,
        "replaced_items": replaced_items,
    }


def _budget_removal_order(*, allow_remove_pump: bool, preference: str) -> list[str]:
    order = list(HOSPITAL_BAG_CART_BUDGET_REMOVE_ORDER)
    if preference == "breastfeeding":
        order = [item_id for item_id in order if item_id not in {"milk-pad", "milk-cream", "milk-storage", "milk-bottle"}] + [
            item_id for item_id in order if item_id in {"milk-pad", "milk-cream", "milk-storage", "milk-bottle"}
        ]
    if allow_remove_pump:
        order.extend(sorted(_hospital_bag_pump_item_ids()))
    return order


def _hospital_bag_budget_message(budget_result: dict[str, Any]) -> str:
    totals = budget_result["totals"]
    target_budget = budget_result.get("target_budget")
    budget_met = bool(budget_result.get("budget_met"))
    removed_names = budget_result.get("removed_item_names") or []
    replaced_items = budget_result.get("replaced_items") or []
    changed_parts: list[str] = []
    if replaced_items:
        changed_parts.append("包被换成基础款")
    if removed_names:
        changed_parts.append("先拿掉" + "、".join(f"「{name}」" for name in removed_names))

    if target_budget is not None and budget_met:
        prefix = f"好，我按 {target_budget:.0f} 元以内帮你压了一版。"
    elif target_budget is not None:
        prefix = f"我先尽量按 {target_budget:.0f} 元以内帮你压了一版，但为了保留吸奶器和基础必需品，目前还会超一点。"
    else:
        prefix = "好，我先帮你切到更省钱的一版。"

    if changed_parts:
        detail = "，".join(changed_parts)
        return f"{prefix}{detail}；吸奶器我先保留。现在预计合计约 {_cart_totals_label(totals)}，共 {totals['itemCount']} 件。"
    return f"{prefix}当前购物车已经比较接近这个要求，吸奶器我先保留。现在预计合计约 {_cart_totals_label(totals)}，共 {totals['itemCount']} 件。"


def _target_budget(value: Any) -> float | None:
    budget = _cart_number(value, default=0)
    if budget <= 0:
        return None
    return round(budget, 2)


def _missing_item_message(action: str) -> str:
    if action == "mark_provided":
        return "医院会提供哪些？你直接告诉我物品名，我帮你从购物车里拿掉。"
    if action == "mark_owned":
        return "家里已经有哪些？你直接告诉我物品名，我帮你从购物车里拿掉。"
    return "你想删哪一件？直接告诉我商品名就行。"


def _remove_items_message(action: str, names: str, totals: dict[str, Any]) -> str:
    if action == "mark_provided":
        return f"好，医院会提供的{names}我先从购物车里拿掉了，现在预计合计 {_cart_totals_label(totals)}。"
    if action == "mark_owned":
        return f"好，家里已经有的{names}我先从购物车里拿掉了，现在预计合计 {_cart_totals_label(totals)}。"
    return f"已帮你从购物车里删掉{names}，现在预计合计 {_cart_totals_label(totals)}。"


def _replace_hospital_bag_cart_items(
    groups: list[dict[str, Any]],
    item_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requested = set(item_ids) if item_ids else set(HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS)
    replaced_items: list[dict[str, Any]] = []
    next_groups: list[dict[str, Any]] = []
    for group in groups:
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            replacement = HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS.get(item_id)
            if item_id in requested and replacement:
                items.append(_clone_hospital_bag_cart_item(replacement))
                replaced_items.append(
                    {
                        "from_item_id": item_id,
                        "from_name": _first_text(item.get("name")),
                        "to_item_id": replacement["id"],
                        "to_name": replacement["name"],
                    }
                )
                continue
            items.append(dict(item))
        next_groups.append({**group, "items": items})
    return next_groups, replaced_items


def _remove_hospital_bag_cart_items(groups: list[dict[str, Any]], item_ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    requested = set(item_ids)
    removed_names: list[str] = []
    next_groups: list[dict[str, Any]] = []
    for group in groups:
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "") in requested:
                name = _first_text(item.get("name"))
                if name:
                    removed_names.append(name)
                continue
            items.append(dict(item))
        next_groups.append({**group, "items": items})
    return next_groups, removed_names


def _restore_hospital_bag_cart_items(groups: list[dict[str, Any]], item_ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    next_groups = _clone_hospital_bag_cart_groups(groups)
    existing_ids = {
        str(item.get("id") or "")
        for group in next_groups
        for item in group.get("items", [])
        if isinstance(item, dict)
    }
    restored_names: list[str] = []
    for item_id in item_ids:
        if item_id in existing_ids:
            continue
        original_id = HOSPITAL_BAG_CART_REPLACEMENT_ORIGINAL_BY_ID.get(item_id, item_id)
        default = _default_hospital_bag_cart_item(original_id)
        if not default:
            continue
        group_title, group_tone, item = default
        group = _find_or_create_hospital_bag_cart_group(next_groups, group_title, group_tone)
        group["items"].append(item)
        existing_ids.add(item["id"])
        restored_names.append(item["name"])
    return next_groups, restored_names


def _update_hospital_bag_cart_quantities(
    groups: list[dict[str, Any]],
    quantity_updates: list[Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    update_by_id: dict[str, int] = {}
    for update in quantity_updates:
        if not isinstance(update, dict):
            continue
        item_id = _first_text(update.get("item_id"))
        if not item_id:
            continue
        update_by_id[item_id] = max(0, int(_cart_number(update.get("qty"), default=1)))

    updated_names: list[str] = []
    removed_names: list[str] = []
    next_groups: list[dict[str, Any]] = []
    for group in groups:
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if item_id not in update_by_id:
                items.append(dict(item))
                continue
            qty = update_by_id[item_id]
            name = _first_text(item.get("name"))
            if qty <= 0:
                if name:
                    removed_names.append(name)
                continue
            next_item = dict(item)
            next_item["qty"] = qty
            items.append(next_item)
            if name:
                updated_names.append(name)
        next_groups.append({**group, "items": items})
    return next_groups, updated_names, removed_names


def _default_hospital_bag_cart_item(item_id: str) -> tuple[str, str, dict[str, Any]] | None:
    product = _momcozy_pump_product(item_id)
    if product:
        return "母乳喂养", "sky", _hospital_bag_cart_pump_item(product)
    for group in DEFAULT_HOSPITAL_BAG_CART_GROUPS:
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "") == item_id:
                return str(group.get("title") or ""), str(group.get("tone") or "rose"), _clone_hospital_bag_cart_item(item)
    return None


def _find_or_create_hospital_bag_cart_group(
    groups: list[dict[str, Any]],
    title: str,
    tone: str,
) -> dict[str, Any]:
    for group in groups:
        if str(group.get("title") or "") == title:
            return group
    group = {"title": title, "tone": tone, "items": []}
    groups.append(group)
    return group


def _ids_for_names(groups: list[dict[str, Any]], names: list[str]) -> list[str]:
    wanted = set(names)
    ids: list[str] = []
    for group in groups:
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            if _first_text(item.get("name")) in wanted:
                ids.append(str(item.get("id") or ""))
    return [item_id for item_id in ids if item_id]


def _cart_number(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def create_birth_plan_form(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    profile_default_values = _birth_prep_shared_default_values(inputs)
    state_default_values = _dict_value(inputs.get("_birth_prep_hospital_bag_slots"))
    arg_default_values = _dict_value(args.get("default_values"))
    default_values = {
        **profile_default_values,
        **_birth_prep_shared_values_from_source(state_default_values),
        **state_default_values,
        **_birth_prep_shared_values_from_source(arg_default_values),
        **arg_default_values,
    }
    fields: list[dict[str, Any]] = []
    for template in BIRTH_PLAN_FORM_FIELDS:
        field = _sanitize_birth_plan_form_field(dict(template))
        value = default_values.get(field["id"])
        if field["id"] == "support_person":
            value = _first_text(value, default_values.get("support_people"))
        normalized_value = _normalize_birth_plan_form_value(field["id"], value)
        if _has_meaningful_value(normalized_value) or (field["id"] == "birth_path" and normalized_value == "还没确定"):
            field["default_value"] = normalized_value
        fields.append(field)
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": "birth_plan_card_intake",
            "title": "信息采集",
            "description": "",
            "submit_label": "生成我的沟通卡",
            "fields": fields,
        },
    }


def create_labor_communication_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    form_data = _confirmed_form_data_for_form(inputs, "birth_plan_card_intake")
    if not _has_any_confirmed_form_data(form_data):
        return _needs_context_result(
            "labor_communication_card_create",
            "needs_confirmed_form_data",
            "生成分娩沟通单前，需要先提交分娩沟通单信息采集表单。",
            ["confirmed_form_data"],
            "请先完成并提交分娩沟通单信息采集表单，我再根据确认后的信息整理沟通单。",
        )
    _persist_birth_prep_profile_memory(inputs, form_data)
    card_json: dict[str, Any] = {
        "card_type": "birth_plan_card",
        "schema_version": "1.0",
        "title": "分娩沟通单",
    }
    assistant_followup = _prepare_birth_plan_card(card_json, inputs, form_data)
    return {
        "tool_name": "labor_communication_card_create",
        "status": "card_created",
        "card": {
            "card_type": "birth_plan_card",
            "schema_version": "1.0",
            "card_json": card_json,
        },
        "assistant_followup": assistant_followup,
    }


def _has_any_confirmed_form_data(form_data: dict[str, Any]) -> bool:
    return any(_has_confirmed_form_value(value) for value in form_data.values())


def create_birth_journey_plan_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    existing_plan = _existing_birth_journey_care_plan(inputs)
    if existing_plan is not None:
        existing_payload = existing_plan.get("payload") if isinstance(existing_plan.get("payload"), dict) else {}
        existing_payload = normalize_birth_journey_plan_payload(existing_payload)
        return {
            "tool_name": "birth_journey_plan_card_create",
            "status": "existing_plan_found",
            "summary": "已存在孕期计划，未重复生成。",
            "side_effect_performed": False,
            "card": {
                "card_type": "birth_journey_plan_card",
                "schema_version": str(existing_payload.get("schema_version") or "1.0"),
                "card_json": existing_payload,
            },
            "plan": existing_plan,
        }

    raw_plan_context = _dict_value(args.get("plan_context")) or _confirmed_form_data(inputs)
    intake_context = _birth_journey_plan_context_from_intake(
        _normalize_birth_journey_intake_state(_dict_value(inputs.get("_birth_journey_intake_state")))
    )
    explicit_context = {**intake_context, **raw_plan_context}
    plan_context = {**_birth_prep_shared_default_values(inputs, explicit_context), **explicit_context}
    missing_context = _missing_birth_journey_required_context(plan_context)
    if missing_context:
        question = _birth_journey_required_context_question(missing_context)
        return {
            "tool_name": "birth_journey_plan_card_create",
            "status": "needs_required_context",
            "summary": "生成孕期计划前，需要先完成分层信息采集。",
            "missing_fields": missing_context,
            "data": {
                "confirmation_question": question,
            },
        }
    scope = str(args.get("scope") or "full").strip() or "full"
    card_json = _build_birth_journey_plan_card_json(plan_context, scope, inputs)
    _persist_birth_prep_profile_memory(inputs, plan_context)
    saved_plan = _save_birth_journey_care_plan(card_json, inputs)
    return {
        "tool_name": "birth_journey_plan_card_create",
        "status": "card_created",
        "card": {
            "card_type": "birth_journey_plan_card",
            "schema_version": "1.0",
            "card_json": card_json,
        },
        "plan": saved_plan,
    }


def delete_birth_journey_plan(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    if args.get("confirmed") is not True:
        return {
            "tool_name": "birth_journey_plan_delete",
            "status": "needs_delete_confirmation",
            "summary": "删除孕期计划前，需要用户明确确认。",
            "side_effect_performed": False,
            "data": {
                "confirmation_question": "确认要删除孕期计划吗？删除后宝宝和我页面不再展示这份计划，需要时可以重新制定。",
            },
        }

    existing_plan = _existing_birth_journey_care_plan(inputs)
    if existing_plan is None:
        return {
            "tool_name": "birth_journey_plan_delete",
            "status": "plan_not_found",
            "summary": "当前没有 active 孕期计划可删除。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
        }

    plan_id = int(existing_plan.get("plan_id") or 0)
    user_profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    user_id = str(inputs.get("user_id") or user_profile.get("user_id") or "").strip()
    deleted = data_store.delete_care_plan_artifact(user_id=user_id, plan_id=plan_id)
    return {
        "tool_name": "birth_journey_plan_delete",
        "status": "plan_deleted" if deleted else "plan_delete_failed",
        "summary": "已删除孕期计划。" if deleted else "删除孕期计划失败。",
        "side_effect_performed": bool(deleted),
        "plan_type": "birth_journey",
        "plan_id": plan_id,
    }


def update_birth_journey_plan_todo(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    existing_plan = _existing_birth_journey_care_plan(inputs)
    if existing_plan is None:
        return {
            "tool_name": "birth_journey_plan_todo_update",
            "status": "plan_not_found",
            "summary": "当前没有 active 孕期计划可更新。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
        }

    refs = _birth_journey_todo_refs_from_args(args)
    if not refs:
        return {
            "tool_name": "birth_journey_plan_todo_update",
            "status": "needs_todo_reference",
            "summary": "需要明确要更新哪一项接下来 7 天行动清单。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
            "plan_id": int(existing_plan.get("plan_id") or 0),
            "data": {
                "confirmation_question": "你想标记完成的是接下来 7 天行动清单里的哪一项？可以告诉我编号或事项名称。",
            },
        }

    user_profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    user_id = str(inputs.get("user_id") or user_profile.get("user_id") or "").strip()
    result = update_birth_journey_plan_todo_completion_for_user(
        user_id=user_id,
        plan_id=int(existing_plan.get("plan_id") or 0),
        item_refs=refs,
        completed=args.get("completed") is not False,
        source="agent",
    )
    return {
        "tool_name": "birth_journey_plan_todo_update",
        **result,
    }


def manage_birth_journey_intake(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    action = str(args.get("action") or "get_state").strip() or "get_state"
    payload = _dict_value(args.get("payload")) or _confirmed_form_data(inputs)
    intake_state = _normalize_birth_journey_intake_state(_dict_value(inputs.get("_birth_journey_intake_state")))
    if action in {"start", "get_state"} and not intake_state.get("started"):
        intake_state["started"] = True
        _merge_birth_journey_entry_context(intake_state, payload, inputs)

    if action == "submit_basic_info":
        basic_info = _birth_journey_basic_info_payload(payload)
        if basic_info:
            intake_state["basic_info"] = {**_dict_value(intake_state.get("basic_info")), **basic_info}
            _persist_birth_prep_profile_memory(inputs, basic_info)
    elif action == "submit_entry_concern":
        intake_state["entry_concern_followup"] = _birth_journey_text_or_skipped(payload, "entry_concern_followup")
    elif action == "mark_checkup_records_uploaded":
        intake_state["checkup_records_uploaded"] = True
        note = _first_text(payload.get("checkup_status"), payload.get("checkup_note"), payload.get("note"))
        intake_state["checkup_status"] = note or "已上传产检记录，等待 CozyMate 整理。"
    elif action == "skip_checkup_records":
        intake_state["checkup_records_uploaded"] = False
        intake_state["checkup_status"] = "未上传产检记录"
    elif action == "submit_risk_factors":
        intake_state["risk_factors"] = _birth_journey_text_or_skipped(payload, "risk_factors")
    elif action == "submit_current_symptoms":
        intake_state["current_symptoms"] = _birth_journey_text_or_skipped(payload, "current_symptoms")
    elif action == "submit_lifestyle_context":
        intake_state["lifestyle_context"] = _birth_journey_text_or_skipped(payload, "lifestyle_context")
    elif action == "submit_feeding_context":
        intake_state["feeding_ibclc_context"] = _birth_journey_text_or_skipped(payload, "feeding_ibclc_context")
        feeding_intention = _first_text(payload.get("feeding_intention"), payload.get("feeding_plan"))
        if feeding_intention:
            intake_state["feeding_intention"] = feeding_intention
    elif action == "complete":
        intake_state["completed"] = True

    next_step = _birth_journey_intake_next_step(intake_state)
    intake_state["next_step"] = next_step
    intake_state["completed_groups"] = _birth_journey_intake_completed_groups(intake_state)
    plan_context = {**_birth_prep_shared_default_values(inputs), **_birth_journey_plan_context_from_intake(intake_state)}
    status = _birth_journey_intake_status(next_step, intake_state)
    result: dict[str, Any] = {
        "tool_name": "birth_journey_intake_manage",
        "status": status,
        "action": action,
        "next_step": next_step,
        "summary": _birth_journey_intake_summary(next_step),
        "intake_state": intake_state,
        "data": {
            "assistant_instruction": _birth_journey_intake_instruction(next_step),
            "confirmation_question": _birth_journey_intake_question(next_step, plan_context),
            "completed_groups": intake_state["completed_groups"],
        },
    }
    if next_step == "basic_info_form":
        result["form"] = _birth_journey_basic_info_form(plan_context, inputs)
    if next_step == "checkup_records_upload":
        result["data"]["upload_panel"] = {
            "title": "上传产检记录",
            "description": "请把目前能找到的产检记录都上传；上传完后告诉我“产检记录上传完毕”。",
            "done_text": "产检记录上传完毕",
        }
    if next_step == "generate_plan":
        result["plan_context"] = plan_context
    return result


def _existing_birth_journey_care_plan(inputs: RuntimeInputs) -> dict[str, Any] | None:
    user_profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    user_id = str(inputs.get("user_id") or user_profile.get("user_id") or "").strip()
    if not user_id:
        return None
    try:
        active_plans = data_store.list_care_plan_artifacts(user_id=user_id, status="active")
    except Exception:
        return None
    for plan in active_plans:
        if isinstance(plan, dict) and plan.get("plan_type") == "birth_journey":
            payload = plan.get("payload")
            if isinstance(payload, dict) and payload:
                return plan
    return None


def _save_birth_journey_care_plan(card_json: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any] | None:
    user_profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    user_id = str(inputs.get("user_id") or user_profile.get("user_id") or "").strip()
    if not user_id:
        return None
    phases = card_json.get("phases") if isinstance(card_json.get("phases"), list) else []
    current_phase = next((phase for phase in phases if isinstance(phase, dict) and phase.get("status") == "current"), None)
    summary_parts = [
        str(card_json.get("subtitle") or "").strip(),
        f"当前阶段：{current_phase.get('title')}" if isinstance(current_phase, dict) and current_phase.get("title") else "",
    ]
    summary = "；".join(part for part in summary_parts if part)
    return data_store.save_care_plan_artifact(
        user_id=user_id,
        plan_type="birth_journey",
        title=str(card_json.get("title") or "孕期计划"),
        summary=summary,
        payload=card_json,
        source_artifact_type="birth_journey_plan_card",
    )


def update_birth_journey_plan_todo_completion_for_user(
    *,
    user_id: str,
    plan_id: int,
    item_refs: list[Any],
    completed: bool,
    source: str = "app",
) -> dict[str, Any]:
    uid = str(user_id or "").strip()
    try:
        pid = int(plan_id)
    except Exception:
        pid = 0
    if not uid or pid <= 0:
        return {
            "status": "missing_user_or_plan",
            "summary": "缺少用户或计划信息，无法更新孕期计划待办。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
            "plan_id": pid,
        }

    plan = data_store.get_care_plan_artifact(user_id=uid, plan_id=pid)
    if not isinstance(plan, dict) or plan.get("plan_type") != "birth_journey" or plan.get("status") != "active":
        return {
            "status": "plan_not_found",
            "summary": "当前没有 active 孕期计划可更新。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
            "plan_id": pid,
        }

    payload = normalize_birth_journey_plan_payload(plan.get("payload") if isinstance(plan.get("payload"), dict) else {})
    todo_items = _birth_journey_next_7_todo_items_from_payload(payload)
    matched_ids, missing_refs, ambiguous_refs = _resolve_birth_journey_todo_item_refs(todo_items, item_refs)
    if ambiguous_refs:
        return {
            "status": "needs_todo_reference",
            "summary": "有些完成事项无法唯一匹配。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
            "plan_id": pid,
            "missing_refs": missing_refs,
            "ambiguous_refs": ambiguous_refs,
            "todo_items": _compact_birth_journey_todo_items(todo_items),
            "data": {
                "confirmation_question": "我不太确定你说的是哪一项，可以告诉我接下来 7 天行动清单里的编号吗？",
            },
        }
    if not matched_ids:
        return {
            "status": "todo_not_found",
            "summary": "没有匹配到要更新的 7 天行动事项。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
            "plan_id": pid,
            "missing_refs": missing_refs,
            "todo_items": _compact_birth_journey_todo_items(todo_items),
            "data": {
                "confirmation_question": "我没有找到对应事项，可以告诉我接下来 7 天行动清单里的编号或完整事项名吗？",
            },
        }

    completed_at = _birth_journey_todo_completed_at() if completed else None
    normalized_source = str(source or "app").strip() or "app"
    updated_items: list[dict[str, Any]] = []
    for item in todo_items:
        if str(item.get("id") or "").strip() not in matched_ids:
            continue
        item["completed"] = bool(completed)
        item["completed_at"] = completed_at
        item["completed_source"] = normalized_source if completed else None
        updated_items.append(dict(item))

    saved_plan = data_store.update_care_plan_artifact_payload(
        user_id=uid,
        plan_id=pid,
        payload=payload,
        summary=str(plan.get("summary") or ""),
    )
    if not saved_plan:
        return {
            "status": "todo_update_failed",
            "summary": "更新孕期计划待办失败。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
            "plan_id": pid,
        }
    return {
        "status": "todo_completion_updated",
        "summary": "已更新接下来 7 天行动清单完成状态。",
        "side_effect_performed": True,
        "plan_type": "birth_journey",
        "plan_id": pid,
        "completed": bool(completed),
        "updated_items": _compact_birth_journey_todo_items(updated_items),
        "todo_items": _compact_birth_journey_todo_items(_birth_journey_next_7_todo_items_from_payload(saved_plan.get("payload") or {})),
        "completion_followups": _birth_journey_completion_followups(updated_items) if completed else [],
        "plan": saved_plan,
    }


BIRTH_JOURNEY_NEXT_7_TODO_PREFIX = "next7_"


def normalize_birth_journey_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload) if isinstance(payload, dict) else {}
    layers = normalized.get("planning_layers")
    if not isinstance(layers, dict):
        return normalized
    normalized_layers = dict(layers)
    next_7 = normalized_layers.get("next_7_days")
    if not isinstance(next_7, dict):
        return normalized
    normalized_next_7 = dict(next_7)
    normalized_items = _normalize_birth_journey_next_7_todo_items(normalized_next_7.get("items"))
    normalized_next_7["items"] = normalized_items
    normalized_next_7["grouped_items"] = _normalize_birth_journey_grouped_next_7_items(
        normalized_next_7.get("grouped_items"),
        normalized_items,
        normalized_layers.get("current_week"),
    )
    normalized_layers["next_7_days"] = normalized_next_7
    normalized["planning_layers"] = normalized_layers
    return normalized


def _birth_journey_next_7_todo_items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalize_birth_journey_plan_payload(payload)
    payload.clear()
    payload.update(normalized)
    layers = payload.get("planning_layers") if isinstance(payload.get("planning_layers"), dict) else {}
    next_7 = layers.get("next_7_days") if isinstance(layers.get("next_7_days"), dict) else {}
    items = next_7.get("items") if isinstance(next_7.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def _birth_journey_todo_refs_from_args(args: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    item_ids = args.get("item_ids")
    if isinstance(item_ids, list):
        refs.extend(item_ids)
    item_numbers = args.get("item_numbers")
    if isinstance(item_numbers, list):
        refs.extend(item_numbers)
    item_refs = args.get("item_refs")
    if isinstance(item_refs, list):
        refs.extend(item_refs)
    return [ref for ref in refs if str(ref or "").strip()]


def _resolve_birth_journey_todo_item_refs(
    items: list[dict[str, Any]],
    refs: list[Any],
) -> tuple[set[str], list[str], list[str]]:
    matched_ids: set[str] = set()
    missing_refs: list[str] = []
    ambiguous_refs: list[str] = []
    by_id = {str(item.get("id") or "").strip(): item for item in items if str(item.get("id") or "").strip()}
    for raw_ref in refs:
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        normalized_id = _birth_journey_todo_ref_to_id(ref)
        if normalized_id in by_id:
            matched_ids.add(normalized_id)
            continue
        title_matches = _birth_journey_todo_title_matches(items, ref)
        if len(title_matches) == 1:
            matched_ids.add(str(title_matches[0].get("id") or "").strip())
        elif len(title_matches) > 1:
            ambiguous_refs.append(ref)
        else:
            missing_refs.append(ref)
    return matched_ids, missing_refs, ambiguous_refs


def _birth_journey_todo_title_matches(items: list[dict[str, Any]], ref: str) -> list[dict[str, Any]]:
    needle = _normalize_birth_journey_todo_match_text(ref)
    if not needle:
        return []
    exact_matches: list[dict[str, Any]] = []
    partial_matches: list[dict[str, Any]] = []
    for item in items:
        title = _normalize_birth_journey_todo_match_text(item.get("title"))
        if not title:
            continue
        if title == needle:
            exact_matches.append(item)
        elif needle in title or title in needle:
            partial_matches.append(item)
    return exact_matches or partial_matches


def _normalize_birth_journey_todo_match_text(value: Any) -> str:
    return re.sub(r"[\s，。；、,.!！?？:：\-_]+", "", str(value or "").strip().lower())


def _birth_journey_todo_ref_to_id(ref: Any) -> str:
    text = str(ref or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"next7_\d{1,2}", text):
        number = int(text.rsplit("_", 1)[-1])
        return _birth_journey_next_7_todo_id(number - 1)
    number_match = re.fullmatch(r"(?:第)?\s*(\d{1,2})\s*(?:项|个)?", text)
    if number_match:
        return _birth_journey_next_7_todo_id(int(number_match.group(1)) - 1)
    return text


def _normalize_birth_journey_next_7_todo_items(value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    normalized_items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        if isinstance(raw_item, str):
            item: dict[str, Any] = {"title": _truncate_birth_journey_plan_text(raw_item, BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS)}
        elif isinstance(raw_item, dict):
            item = dict(raw_item)
        else:
            continue
        title = str(item.get("title") or "").strip()
        base_title = _birth_journey_title_without_priority_prefix(title)
        if not base_title:
            continue
        based_on = item.get("based_on") if isinstance(item.get("based_on"), list) else []
        legacy_upgrade = _birth_journey_legacy_next_7_item_upgrade(base_title)
        if legacy_upgrade:
            base_title = str(legacy_upgrade.get("title") or base_title).strip()
            for key in ("reason", "steps", "done_criteria", "after_done_value", "completion_followup"):
                if key in legacy_upgrade:
                    item[key] = legacy_upgrade[key]
        priority_type = str(item.get("priority_type") or "").strip()
        priority_label = str(item.get("priority_label") or "").strip()
        if priority_type not in BIRTH_JOURNEY_PRIORITY_LABELS or not priority_label:
            priority_type, priority_label = _birth_journey_plan_item_priority(base_title, based_on)
            item["priority_type"] = priority_type
            item["priority_label"] = priority_label
        else:
            item["priority_label"] = BIRTH_JOURNEY_PRIORITY_LABELS.get(priority_type, priority_label)
        item["title"] = _birth_journey_visible_priority_title(base_title, priority_type)
        if not isinstance(item.get("steps"), list) or not item.get("steps"):
            item["steps"] = _birth_journey_plan_item_steps(base_title, based_on)
        if not str(item.get("done_criteria") or "").strip():
            item["done_criteria"] = _birth_journey_done_criteria(base_title)
        if not str(item.get("after_done_value") or "").strip():
            item["after_done_value"] = _birth_journey_after_done_value(base_title, based_on)
        if not str(item.get("completion_followup") or "").strip():
            item["completion_followup"] = _birth_journey_completion_followup(base_title, str(item.get("after_done_value") or ""))
        item["id"] = _birth_journey_todo_ref_to_id(item.get("id")) or _birth_journey_next_7_todo_id(len(normalized_items))
        completed = _birth_journey_completed_bool(item.get("completed"))
        item["completed"] = completed
        item["completed_at"] = (str(item.get("completed_at") or "").strip() or None) if completed else None
        item["completed_source"] = (str(item.get("completed_source") or "").strip() or None) if completed else None
        normalized_items.append(item)
    return normalized_items


def _birth_journey_next_7_todo_id(index: int) -> str:
    return f"{BIRTH_JOURNEY_NEXT_7_TODO_PREFIX}{max(1, int(index) + 1):02d}"


def _birth_journey_completed_bool(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "done", "completed", "完成", "已完成"}
    return bool(value) if isinstance(value, int) else False


def _birth_journey_todo_completed_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _compact_birth_journey_todo_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        compact_items.append(
            {
                "id": str(item.get("id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "priority_type": str(item.get("priority_type") or "").strip() or None,
                "priority_label": str(item.get("priority_label") or "").strip() or None,
                "completed": _birth_journey_completed_bool(item.get("completed")),
                "completed_at": str(item.get("completed_at") or "").strip() or None,
                "done_criteria": str(item.get("done_criteria") or "").strip() or None,
                "after_done_value": str(item.get("after_done_value") or "").strip() or None,
                "completion_followup": str(item.get("completion_followup") or "").strip() or None,
            }
        )
    return compact_items


def _birth_journey_completion_followups(items: list[dict[str, Any]]) -> list[str]:
    followups: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("completion_followup") or item.get("after_done_value") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        followups.append(text)
        if len(followups) >= 2:
            break
    return followups


BIRTH_JOURNEY_BASIC_INFO_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "id": "current_week",
        "label": "当前孕周",
        "type": "text",
        "required": True,
        "help_text": None,
        "placeholder": "例如：28周、28+3",
        "default_value": None,
        "options": None,
    },
    {
        "id": "ivf",
        "label": "是否 IVF（体外受精）",
        "type": "select",
        "required": False,
        "help_text": None,
        "placeholder": None,
        "default_value": None,
        "options": ["是", "否", "不确定/暂不说"],
    },
    {
        "id": "fetus_count",
        "label": "单胎/双胎",
        "type": "select",
        "required": True,
        "help_text": None,
        "placeholder": None,
        "default_value": None,
        "options": ["单胎", "双胎", "多胎", "不确定/暂不说"],
    },
    {
        "id": "age",
        "label": "年龄",
        "type": "number",
        "required": True,
        "help_text": None,
        "placeholder": "例如：32",
        "default_value": None,
        "options": None,
    },
    {
        "id": "height_cm",
        "label": "身高",
        "type": "number",
        "required": False,
        "help_text": None,
        "placeholder": "cm",
        "default_value": None,
        "options": None,
    },
    {
        "id": "pre_pregnancy_weight_kg",
        "label": "孕前体重",
        "type": "number",
        "required": False,
        "help_text": None,
        "placeholder": "kg",
        "default_value": None,
        "options": None,
    },
    {
        "id": "current_weight_kg",
        "label": "当前体重",
        "type": "number",
        "required": False,
        "help_text": None,
        "placeholder": "kg",
        "default_value": None,
        "options": None,
    },
    {
        "id": "city_or_country",
        "label": "所在城市/国家",
        "type": "text",
        "required": False,
        "help_text": None,
        "placeholder": "例如：上海 / 美国加州",
        "default_value": None,
        "options": None,
    },
    {
        "id": "birth_hospital",
        "label": "建档医院",
        "type": "text",
        "required": False,
        "help_text": None,
        "placeholder": "如果还没建档，可以写“还没确定”",
        "default_value": None,
        "options": None,
    },
)

BIRTH_JOURNEY_BASIC_INFO_FIELD_IDS = tuple(field["id"] for field in BIRTH_JOURNEY_BASIC_INFO_FIELDS)


BIRTH_JOURNEY_SURVEY_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "id": "basic_info",
        "label": "孕周与基本情况",
        "question": "请先填写孕周与基本情况表单；当前孕周请尽量填写，其它不清楚的可以留空。",
        "keys": (
            "due_date_or_week",
            "current_week",
            "ivf",
            "fetus_count",
            "age",
            "height_cm",
            "pre_pregnancy_weight_kg",
            "current_weight_kg",
            "city_or_country",
            "birth_hospital",
            "hospital",
        ),
    },
    {
        "id": "checkup_records",
        "label": "产检记录",
        "question": "请上传目前全部产检记录；上传完后告诉我“产检记录上传完毕”。",
        "keys": ("checkup_records_uploaded", "checkup_status", "checkup_records", "uploaded_checkup_records"),
    },
    {
        "id": "risk_factors",
        "label": "孕期高风险因素",
        "question": "你了解自己是否有什么孕期高风险因素吗，比如慢性高血压、糖尿病、肾病、自身免疫病、甲状腺病、心脏病，或既往剖宫产、早产/流产史等？",
        "keys": ("risk_factors", "high_risk_factors", "pregnancy_history_or_notes", "medical_notes", "special_notes", "doctor_notes"),
    },
    {
        "id": "current_symptoms",
        "label": "当前不适或异常",
        "question": "那你现在有没有一些不舒服或异常情况？比如阴道流血/流水、腹痛、发热、严重呕吐、头痛、视物模糊、胸痛气短、手脸明显水肿、胎动变化，或情绪崩溃、自伤想法。",
        "keys": ("current_symptoms", "symptoms", "discomforts", "urgent_symptoms"),
    },
    {
        "id": "lifestyle_context",
        "label": "生活和工作场景",
        "question": "结合你现在的孕周，我再少量了解会影响执行的生活场景：饮食/补剂、运动/睡眠、久站通勤、家庭支持、焦虑点里，哪些比较需要我纳入计划？",
        "keys": ("lifestyle_context", "work_context", "sleep_context", "exercise_context", "family_support", "budget", "top_worries", "first_birth", "support_person"),
    },
    {
        "id": "feeding_ibclc_context",
        "label": "喂养和 IBCLC 相关信息",
        "question": "最后想了解喂养准备：你是否计划母乳/混合/配方？是否可能需要背奶？预计产假多久？之前有没有低奶量、乳腺炎、宝宝含乳困难的经历？是否可能早产、剖宫产？",
        "keys": ("feeding_ibclc_context", "feeding_intention", "feeding_plan", "pump_plan", "ibclc_plan", "lactation_history"),
    },
)


def _normalize_birth_journey_intake_state(value: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(value or {})
    if not isinstance(state.get("basic_info"), dict):
        state["basic_info"] = {}
    return state


def _birth_journey_basic_info_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = _dict_value(payload.get("basic_info")) or payload
    return {
        field_id: source[field_id]
        for field_id in BIRTH_JOURNEY_BASIC_INFO_FIELD_IDS
        if _has_meaningful_value(source.get(field_id))
    }


def _birth_journey_text_or_skipped(payload: dict[str, Any], field_id: str) -> str:
    text = _first_answer_text(
        payload.get(field_id),
        payload.get("answer"),
        payload.get("text"),
        payload.get("note"),
        payload.get("content"),
    )
    return text or "跳过"


def _merge_birth_journey_entry_context(state: dict[str, Any], payload: dict[str, Any], inputs: RuntimeInputs) -> None:
    entry_reason = _first_answer_text(
        payload.get("entry_reason"),
        payload.get("initial_message"),
        payload.get("user_message"),
        payload.get("reason"),
    )
    current_message = _first_answer_text(inputs.get("user_message"))
    if not entry_reason and _birth_journey_entry_message_has_signal(current_message):
        entry_reason = current_message
    if entry_reason:
        state["entry_reason"] = entry_reason

    concerns = _birth_journey_initial_concerns(payload, entry_reason)
    if concerns:
        state["initial_concerns"] = concerns

    known_values = {
        **_birth_journey_known_values_from_text(entry_reason),
        **_birth_journey_known_values_from_payload(payload),
    }
    if known_values:
        state["entry_known_values"] = known_values


def _birth_journey_initial_concerns(payload: dict[str, Any], entry_reason: str) -> list[str]:
    values: list[str] = []
    raw = payload.get("initial_concerns") or payload.get("concerns") or payload.get("top_worries")
    if isinstance(raw, list):
        values.extend(str(item).strip() for item in raw if _has_meaningful_value(item))
    elif _has_meaningful_value(raw):
        values.append(str(raw).strip())
    if _birth_journey_entry_message_has_signal(entry_reason):
        values.append(entry_reason)
    return _unique_text_list(values, 5)


def _birth_journey_entry_message_has_signal(text: str) -> bool:
    return any(
        token in text
        for token in (
            "焦虑",
            "无助",
            "迷茫",
            "心里没底",
            "不知道",
            "怎么办",
            "先做什么",
            "怕漏",
            "漏事",
            "手忙脚乱",
            "慌",
            "压力",
        )
    )


def _birth_journey_explicit_emotion_label(text: str) -> str:
    emotion_labels = (
        ("焦虑", "焦虑"),
        ("心里没底", "心里没底"),
        ("压力", "压力"),
        ("无助", "无助"),
        ("迷茫", "迷茫"),
        ("慌", "慌乱感"),
    )
    for token, label in emotion_labels:
        if token in text:
            return label
    return ""


def _birth_journey_text_has_explicit_worry(text: str) -> bool:
    return any(token in text for token in ("担心", "担忧", "害怕", "怕"))


def _birth_journey_known_values_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = _dict_value(payload.get("known_values")) or payload
    known: dict[str, Any] = {}
    for field_id in ("age", "current_week", "due_date_or_week"):
        if _has_meaningful_value(source.get(field_id)):
            known[field_id] = source[field_id]
    return known


def _birth_journey_known_values_from_text(text: str) -> dict[str, str]:
    known: dict[str, str] = {}
    age_match = re.search(r"(\d{2})\s*岁", text)
    if age_match:
        known["age"] = age_match.group(1)
    week_match = re.search(r"(?:怀孕|孕)?\s*(\d{1,2})(?:\s*\+\s*(\d{1,2}))?\s*周", text)
    if week_match:
        known["current_week"] = f"{week_match.group(1)}+{week_match.group(2)}周" if week_match.group(2) else f"{week_match.group(1)}周"
    return known


def _birth_journey_entry_context_text(state: dict[str, Any]) -> str:
    values: list[Any] = [state.get("entry_reason")]
    concerns = state.get("initial_concerns")
    if isinstance(concerns, list):
        values.extend(concerns)
    values.append(state.get("entry_concern_followup"))
    return "；".join(_unique_text_list(values, 8))


def _birth_journey_intake_completed_groups(state: dict[str, Any]) -> list[str]:
    groups: list[str] = []
    if _dict_value(state.get("basic_info")):
        groups.append("basic_info")
    if "entry_concern_followup" in state:
        groups.append("entry_concern")
    if state.get("checkup_records_uploaded") is True or _has_meaningful_value(state.get("checkup_status")):
        groups.append("checkup_records")
    for field_id in ("risk_factors", "current_symptoms", "lifestyle_context", "feeding_ibclc_context"):
        if field_id in state:
            groups.append(field_id)
    return groups


def _birth_journey_intake_next_step(state: dict[str, Any]) -> str:
    if not _dict_value(state.get("basic_info")):
        return "basic_info_form"
    if state.get("checkup_records_uploaded") is not True and "checkup_status" not in state:
        return "checkup_records_upload"
    if "risk_factors" not in state:
        return "risk_question"
    if "current_symptoms" not in state:
        return "symptom_question"
    if _birth_journey_symptoms_need_pause(str(state.get("current_symptoms") or "")):
        return "pause_for_symptoms"
    if "lifestyle_context" not in state:
        return "lifestyle_question"
    if "feeding_ibclc_context" not in state:
        return "feeding_question"
    return "generate_plan"


def _birth_journey_intake_status(next_step: str, state: dict[str, Any]) -> str:
    if next_step == "pause_for_symptoms":
        return "blocked_by_symptoms"
    if next_step == "generate_plan":
        return "ready_to_generate"
    return "in_progress"


def _birth_journey_symptoms_need_pause(text: str) -> bool:
    normalized = text.strip()
    if not normalized or _normalized_placeholder(normalized) in PLACEHOLDER_VALUES:
        return False
    if any(token in normalized for token in ("没有", "无", "暂时没有", "没什么", "正常")) and not any(
        token in normalized for token in ("但是", "不过", "除了")
    ):
        return False
    return any(
        token in normalized
        for token in (
            "出血",
            "流血",
            "流水",
            "破水",
            "腹痛",
            "发热",
            "呕吐",
            "头痛",
            "视物",
            "胸痛",
            "气短",
            "水肿",
            "胎动",
            "情绪崩溃",
            "自伤",
        )
    )


def _birth_journey_intake_summary(next_step: str) -> str:
    summaries = {
        "basic_info_form": "需要先填写孕周与基本情况表单。",
        "checkup_records_upload": "基础信息已记录，下一步需要上传产检记录。",
        "risk_question": "产检记录上传状态已确认，下一步补问孕期高风险因素。",
        "symptom_question": "高风险因素已问到，下一步确认当前不适或异常。",
        "pause_for_symptoms": "用户报告了需要先处理的当前症状，暂停生成孕期计划。",
        "lifestyle_question": "当前症状已确认，下一步少量了解生活方式与场景。",
        "feeding_question": "生活场景已问到，下一步确认喂养和 IBCLC 相关信息。",
        "generate_plan": "孕期计划信息采集已完成，可以调用 birth_journey_plan_card_create。",
    }
    return summaries.get(next_step, "继续推进孕期计划信息采集。")


def _birth_journey_intake_instruction(next_step: str) -> str:
    instructions = {
        "basic_info_form": "最终回复说明基础信息表已打开，并温和解释这是为了后面更贴合用户情况地整理孕期计划；请用户简单填写知道的部分，不确定的地方可以选“不确定/暂不说”。不要在聊天里逐项追问这些字段。",
        "checkup_records_upload": "请用户上传最新一次的产检记录，如果没有或者不在手边也可以先跳过。",
        "risk_question": "只补问孕期高风险因素这一件事；用户不清楚也可以说不清楚。",
        "symptom_question": "只补问当前不适或异常这一件事；如果用户确认有明显异常，先不要生成计划。",
        "pause_for_symptoms": "先承接用户情况，建议优先联系医生/医院确认；不要继续生成孕期计划。",
        "lifestyle_question": "根据用户孕周少量追问生活方式与场景，不要变成长问卷。",
        "feeding_question": "一次性问完喂养和 IBCLC 相关信息，允许用户跳过。",
        "generate_plan": "直接调用 birth_journey_plan_card_create，plan_context 使用本工具返回的 plan_context；工具调用前不要先输出路线图。",
    }
    return instructions.get(next_step, "按 next_step 继续推进。")


def _birth_journey_intake_question(next_step: str, plan_context: dict[str, Any]) -> str:
    if next_step == "risk_question":
        return "你了解自己是否有什么孕期高风险因素吗，比如慢性高血压、糖尿病、肾病、自身免疫病、甲状腺病、心脏病，或既往剖宫产、早产/流产史等？不清楚也可以先跳过。"
    if next_step == "symptom_question":
        return "那你现在有没有一些不舒服或异常情况？比如阴道流血、腹痛、发热、呕吐、头痛、视物模糊、胸痛气短、手脸明显水肿、胎动变化等。"
    if next_step == "lifestyle_question":
        week_text = str(plan_context.get("due_date_or_week") or plan_context.get("current_week") or "").strip()
        prefix = f"结合你现在{week_text}，" if week_text else ""
        return prefix + "我再了解一下可能会影响孕期计划的生活习惯活生活方式，比如饮食、运动、睡眠、工作、家庭支持等，这些里面如果有什么想对我说的也可以告诉我"
    if next_step == "feeding_question":
        return "最后再了解一下喂养计划：你是否计划母乳/混合/配方？预计产假多久？之前有没有低奶量、乳腺炎、宝宝含乳困难的经历？是否存在早产风险？计划剖宫产还是顺产？不确定的可以跳过。"
    if next_step == "pause_for_symptoms":
        return "针对你挡下的这种情况。我建议可以先暂停制定计划，优先按医生或医院建议处理当前症状。"
    return ""


def _birth_journey_context_age(context: dict[str, Any]) -> int | None:
    try:
        age = int(str(context.get("age") or "").strip())
    except (TypeError, ValueError):
        return None
    return age if 12 <= age <= 60 else None


def birth_journey_intake_quick_reply_guidance(next_step: str) -> list[dict[str, str]]:
    replies_by_step = {
        "checkup_records_upload": ("产检记录上传完毕", "先跳过这步", "我现在没有记录"),
        "risk_question": ("没有高风险因素", "不清楚先跳过", "有一些风险因素"),
        "symptom_question": ("目前没有异常", "有些不舒服", "不确定先跳过"),
        "lifestyle_question": ("睡眠需要纳入计划", "工作通勤有压力", "先跳过这步"),
        "feeding_question": ("计划母乳喂养", "还不确定先跳过", "可能需要背奶"),
    }
    texts = replies_by_step.get(str(next_step or "").strip())
    if not texts:
        return []
    return [{"text": text} for text in texts]


def _birth_journey_basic_info_form(plan_context: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    default_city_or_country = _birth_journey_default_city_or_country(inputs)
    default_values = {key: value for key, value in plan_context.items() if key in BIRTH_JOURNEY_BASIC_INFO_FIELD_IDS}
    for template in BIRTH_JOURNEY_BASIC_INFO_FIELDS:
        field = dict(template)
        value = _first_text(plan_context.get(field["id"]))
        if field["id"] == "current_week" and not value:
            value = _birth_journey_default_current_week(plan_context)
        if field["id"] == "city_or_country" and not value:
            value = default_city_or_country
        if _has_meaningful_value(value):
            field["default_value"] = value
            default_values[field["id"]] = value
        fields.append(field)
    return {
        "id": "birth_journey_basic_info_intake",
        "title": "孕周与基本情况",
        "description": "先填写几项基础信息，后面我会按你的孕周、身体情况和准备状态来整理更贴合你的孕期计划。",
        "submit_label": "提交",
        "fields": fields,
        "default_values": default_values,
    }


def _birth_journey_default_current_week(plan_context: dict[str, Any]) -> str:
    text = _first_text(
        plan_context.get("current_week"),
        plan_context.get("due_date_or_week"),
        plan_context.get("birth_prep_due_date_or_week"),
        plan_context.get("gestational_week"),
    )
    if not text:
        return ""
    week_match = re.search(r"(?:孕\s*)?(\d{1,2})(?:\s*\+\s*\d{1,2})?\s*周", text)
    if week_match:
        return text
    bare_week_match = re.search(r"^(\d{1,2})(?:\s*\+\s*\d{1,2})?$", text)
    if bare_week_match:
        return f"{text}周"
    return ""


def _birth_journey_default_city_or_country(inputs: RuntimeInputs) -> str:
    profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    return _first_text(
        inputs.get("city_or_country"),
        inputs.get("region"),
        inputs.get("city"),
        inputs.get("country"),
        inputs.get("location"),
        profile.get("city_or_country"),
        profile.get("region"),
        profile.get("city"),
        profile.get("country"),
        profile.get("location"),
        "深圳",
    )


def _birth_journey_plan_context_from_intake(state: dict[str, Any]) -> dict[str, Any]:
    basic_info = _dict_value(state.get("basic_info"))
    context: dict[str, Any] = {**_dict_value(state.get("entry_known_values")), **basic_info}
    due_or_week = _first_text(
        basic_info.get("due_date_or_week"),
        basic_info.get("due_date"),
        basic_info.get("current_week"),
        context.get("due_date_or_week"),
        context.get("current_week"),
        _birth_journey_due_date_from_lmp(basic_info.get("last_menstrual_period")),
    )
    if due_or_week:
        context["due_date_or_week"] = due_or_week
    if _has_meaningful_value(state.get("checkup_status")):
        context["checkup_status"] = state["checkup_status"]
    elif state.get("checkup_records_uploaded") is True:
        context["checkup_status"] = "已上传产检记录，等待 CozyMate 整理。"
    if state.get("checkup_records_uploaded") is True:
        context["checkup_records_uploaded"] = "是"
    for key in ("risk_factors", "current_symptoms", "lifestyle_context", "feeding_ibclc_context", "feeding_intention"):
        if key in state:
            context[key] = state[key]
    if _has_meaningful_value(state.get("entry_reason")):
        context["entry_reason"] = state["entry_reason"]
    initial_concerns = state.get("initial_concerns")
    if isinstance(initial_concerns, list) and initial_concerns:
        context["initial_concerns"] = initial_concerns
    entry_followup = _first_answer_text(state.get("entry_concern_followup"))
    entry_context_text = _birth_journey_entry_context_text(state)
    if entry_followup:
        context["entry_concern_followup"] = entry_followup
    if entry_context_text:
        context["top_worries"] = _first_answer_text(entry_followup, initial_concerns, state.get("entry_reason"))
    if entry_followup:
        existing_lifestyle = _first_answer_text(context.get("lifestyle_context"))
        context["lifestyle_context"] = (
            f"{existing_lifestyle}；前期关键担心：{entry_followup}" if existing_lifestyle else entry_followup
        )
    return context


def _birth_journey_due_date_from_lmp(value: Any) -> str:
    lmp = _date_from_text(str(value or ""))
    if lmp is None:
        return ""
    return _format_birth_journey_date(lmp + timedelta(days=280))


def _missing_birth_journey_required_context(form_data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in BIRTH_JOURNEY_SURVEY_FIELDS:
        if not _birth_journey_context_field_was_asked(form_data, field["keys"]):
            missing.append(field["id"])
    return missing


def _birth_journey_context_field_was_asked(form_data: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(key in form_data for key in keys)


def _birth_journey_has_answer(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and _normalized_placeholder(text) not in PLACEHOLDER_VALUES


def _first_answer_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if _birth_journey_has_answer(item))
        elif isinstance(value, dict):
            text = ", ".join(
                str(nested_value).strip()
                for nested_value in value.values()
                if _birth_journey_has_answer(nested_value)
            )
        else:
            text = str(value or "").strip()
        if _birth_journey_has_answer(text):
            return text
    return ""


def _normalize_birth_journey_birth_path(value: str) -> str:
    text = str(value or "").strip()
    normalized = _normalize_birth_path(text)
    return normalized if _birth_journey_has_answer(normalized) else text


def _birth_journey_required_context_question(missing_fields: list[str]) -> str:
    fields_by_id = {field["id"]: field for field in BIRTH_JOURNEY_SURVEY_FIELDS}
    questions = [
        str(fields_by_id[field_id]["question"])
        for field_id in missing_fields
        if field_id in fields_by_id
    ]
    if not questions:
        return "我还需要把影响计划的情况问到；不清楚或不想说的部分可以直接写“跳过”。"
    intro = "我先把会影响计划的情况都问到；你只答知道的就好，不清楚或不想说的可以写“跳过”。"
    lines = [f"{index + 1}. {question}" for index, question in enumerate(questions)]
    return intro + "\n" + "\n".join(lines)


def _build_birth_journey_plan_card_json(form_data: dict[str, Any], scope: str, inputs: RuntimeInputs) -> dict[str, Any]:
    today = _message_date(inputs)
    due_text = _first_text(
        form_data.get("due_date_or_week"),
        form_data.get("due_date"),
        form_data.get("current_week"),
        _birth_journey_due_date_from_lmp(form_data.get("last_menstrual_period")),
    )
    timeline = _birth_journey_timeline(due_text, today, inputs, scope)
    first_birth = _normalize_first_birth(_first_text(form_data.get("first_birth")))
    fetus_count = _first_text(form_data.get("fetus_count"), form_data.get("baby_count"))
    birth_path = _normalize_birth_journey_birth_path(_first_answer_text(form_data.get("birth_path"), form_data.get("delivery_method")))
    feeding_intention = _normalize_feeding_intention(_first_text(form_data.get("feeding_intention"), form_data.get("feeding_plan")))
    birth_setting = _first_text(form_data.get("birth_setting"), form_data.get("birth_hospital"), form_data.get("hospital"))
    support_person = _first_answer_text(form_data.get("support_person"), form_data.get("support_people"), form_data.get("partner_or_support"))
    checkup_status = _first_answer_text(
        form_data.get("checkup_status"),
        form_data.get("established_record"),
        form_data.get("next_checkup_time"),
        form_data.get("completed_checks"),
        form_data.get("abnormal_results"),
        form_data.get("checkup_records"),
    )
    current_symptoms = _unique_text_list(
        form_data.get("current_symptoms") or form_data.get("symptoms") or form_data.get("discomforts") or form_data.get("urgent_symptoms"),
        8,
    )
    risk_factors = _unique_text_list(
        form_data.get("risk_factors") or form_data.get("high_risk_factors"),
        8,
    )
    age = _first_text(form_data.get("age"), form_data.get("birth_prep_age"))
    top_worries = _first_answer_text(form_data.get("top_worries"), form_data.get("birth_prep_top_worries"))
    entry_reason = _first_answer_text(
        form_data.get("entry_reason"),
        form_data.get("initial_message"),
        form_data.get("user_message"),
    )
    initial_concerns = _unique_text_list(
        form_data.get("initial_concerns") or form_data.get("concerns"),
        6,
    )
    entry_concern_followup = _first_answer_text(form_data.get("entry_concern_followup"))
    lifestyle_context = _first_answer_text(
        form_data.get("lifestyle_context"),
        form_data.get("work_context"),
        form_data.get("sleep_context"),
        form_data.get("exercise_context"),
        form_data.get("family_support"),
        form_data.get("budget"),
        form_data.get("top_worries"),
    )
    feeding_ibclc_context = _first_answer_text(
        form_data.get("feeding_ibclc_context"),
        form_data.get("pump_plan"),
        form_data.get("ibclc_plan"),
        form_data.get("lactation_history"),
    )
    medical_notes = _text_list(
        form_data.get("pregnancy_history_or_notes")
        or form_data.get("medical_notes")
        or form_data.get("special_notes")
        or form_data.get("doctor_notes")
    )
    context = {
        "first_birth": first_birth,
        "fetus_count": fetus_count,
        "birth_path": birth_path,
        "feeding_intention": feeding_intention,
        "feeding_ibclc_context": feeding_ibclc_context,
        "birth_setting": birth_setting,
        "support_person": support_person,
        "checkup_status": checkup_status,
        "current_symptoms": current_symptoms,
        "risk_factors": risk_factors,
        "age": age,
        "top_worries": top_worries,
        "entry_reason": entry_reason,
        "initial_concerns": initial_concerns,
        "entry_concern_followup": entry_concern_followup,
        "lifestyle_context": lifestyle_context,
        "medical_notes": medical_notes,
        "current_week": timeline.get("current_week"),
    }
    phases = [_birth_journey_phase_payload(spec, context) for spec in timeline["phase_specs"]]
    _mark_birth_journey_current_phase(phases)
    planning_layers = _birth_journey_planning_layers(timeline, context, phases)
    owner = {
        "due_date_or_week": due_text or "待确认",
        "current_week": f"孕{timeline['current_week']}周" if timeline.get("current_week") else "",
        "estimated_due_date": _format_birth_journey_date(timeline.get("due_date")) if timeline.get("due_date") else "",
        "birth_path": birth_path,
        "birth_setting": birth_setting,
        "support_person": support_person,
        "feeding_intention": feeding_intention,
    }
    owner = {key: value for key, value in owner.items() if _birth_journey_owner_has_value(key, value)}
    return {
        "card_type": "birth_journey_plan_card",
        "schema_version": "1.0",
        "title": "孕期计划",
        "subtitle": _birth_journey_subtitle(timeline, scope),
        "owner": owner,
        "planning_layers": planning_layers,
        "phases": phases,
        "next_action": _birth_journey_next_action(timeline, context),
        "disclaimer": "这份计划用于准备和沟通，不能替代医生、助产士或医院的具体建议；有破水、出血、胎动明显减少、规律宫缩加密或明显不适时，请按医院或医生指导处理。",
    }


def _birth_journey_owner_has_value(key: str, value: Any) -> bool:
    if key in {"birth_path", "support_person"}:
        return _birth_journey_has_answer(value)
    return _has_meaningful_value(value)


def _birth_journey_timeline(due_text: str, today: date, inputs: RuntimeInputs, scope: str) -> dict[str, Any]:
    gestational_days = _birth_journey_gestational_days(due_text, today, inputs)
    due_date = _birth_journey_due_date(due_text, today, gestational_days)
    estimated = due_date is not None and not _date_from_text(due_text)
    current_week = gestational_days // 7 if gestational_days is not None else None
    phase_specs = _birth_journey_phase_specs(due_date, gestational_days, today, estimated, scope)
    return {
        "due_date": due_date,
        "estimated": estimated,
        "current_week": current_week,
        "phase_specs": phase_specs,
    }


def _birth_journey_phase_specs(
    due_date: date | None,
    gestational_days: int | None,
    today: date,
    estimated: bool,
    scope: str,
) -> list[dict[str, Any]]:
    if due_date is None:
        specs = [
            {"id": "late_pregnancy", "title": "孕晚期", "date_range": "补充孕周后换算具体日期", "is_current": True},
            {"id": "labor_recognition", "title": "临产阶段", "date_range": "补充孕周后换算具体日期"},
            {"id": "hospital_birth", "title": "住院分娩", "date_range": "入院当天～出院当天"},
            {"id": "postpartum", "title": "产后恢复", "date_range": "出院后 0～42 天"},
        ]
        return _limit_birth_journey_phase_specs(specs, scope)

    raw_specs: list[dict[str, Any]] = []
    early_start = due_date - timedelta(days=280)
    early_end = due_date - timedelta(days=183)
    mid_start = due_date - timedelta(days=182)
    late_start = due_date - timedelta(days=84)
    late_end = due_date - timedelta(days=22)
    labor_start = due_date - timedelta(days=21)
    labor_end = due_date + timedelta(days=7)

    if gestational_days is not None and gestational_days < 14 * 7 and today <= early_end:
        raw_specs.append(
            {
                "id": "early_pregnancy",
                "title": "孕早期",
                "start_date": early_start,
                "end_date": early_end,
                "is_current": True,
            }
        )
    if gestational_days is not None and gestational_days < 28 * 7 and today < late_start:
        raw_specs.append(
            {
                "id": "mid_pregnancy",
                "title": "孕中期",
                "start_date": mid_start,
                "end_date": late_start - timedelta(days=1),
                "is_current": today >= mid_start,
            }
        )
    raw_specs.extend(
        [
            {"id": "late_pregnancy", "title": "孕晚期", "start_date": late_start, "end_date": late_end},
            {"id": "labor_recognition", "title": "临产阶段", "start_date": labor_start, "end_date": labor_end},
            {"id": "hospital_birth", "title": "住院分娩", "date_range": "入院当天～出院当天"},
            {"id": "postpartum", "title": "产后恢复", "date_range": "出院后 0～42 天"},
        ]
    )

    specs: list[dict[str, Any]] = []
    for spec in raw_specs:
        start_date = spec.get("start_date")
        end_date = spec.get("end_date")
        if isinstance(end_date, date) and end_date < today and spec["id"] not in {"hospital_birth", "postpartum"}:
            continue
        display_spec = dict(spec)
        if isinstance(start_date, date) and isinstance(end_date, date):
            display_start = max(today, start_date) if start_date <= today <= end_date else start_date
            display_spec["date_range"] = _birth_journey_date_range(display_start, end_date, estimated)
            display_spec["is_current"] = display_spec.get("is_current") or (start_date <= today <= end_date)
        specs.append(display_spec)

    if not any(spec.get("is_current") for spec in specs) and specs:
        specs[0]["is_current"] = True
    return _limit_birth_journey_phase_specs(specs, scope)


def _limit_birth_journey_phase_specs(specs: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    if scope == "short_range":
        return specs[:2]
    if scope == "prenatal_only":
        return [spec for spec in specs if spec["id"] not in {"hospital_birth", "postpartum"}][:4]
    return specs[:6]


def _birth_journey_phase_payload(spec: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    phase = _birth_journey_base_phase(spec["id"])
    phase.update(
        {
            "id": spec["id"],
            "title": spec["title"],
            "date_range": spec.get("date_range") or "",
            "status": "current" if spec.get("is_current") else "upcoming",
        }
    )
    _personalize_birth_journey_phase(phase, context)
    for key in ("watchouts", "actions", "comate_help"):
        phase[key] = _unique_birth_journey_items(phase.get(key), 4)
    return phase


def _birth_journey_planning_layers(
    timeline: dict[str, Any],
    context: dict[str, Any],
    phases: list[dict[str, Any]],
) -> dict[str, Any]:
    week = timeline.get("current_week")
    current_phase = next((phase for phase in phases if isinstance(phase, dict) and phase.get("status") == "current"), phases[0] if phases else {})
    current_phase_title = str(current_phase.get("title") or "").strip()
    safety_items = _birth_journey_safety_items(context)
    current_items = _unique_birth_journey_plan_items(
        [*safety_items, *_birth_journey_current_focus_items(week, context, current_phase)],
    )
    next_7_items = _normalize_birth_journey_next_7_todo_items(_birth_journey_next_7_day_items(week, context))
    next_7_context_reason = _birth_journey_next_7_context_reason(week, context)
    next_7_grouped_items = _birth_journey_grouped_next_7_items(next_7_items, week, context)
    next_2_4_weeks = _birth_journey_next_2_4_week_items(week, context)
    later_milestones = _birth_journey_later_milestones(week, context)
    return {
        "current_week": week,
        "current_phase_title": current_phase_title,
        "plan_basis": {
            "title": "为什么这样安排",
            "items": _birth_journey_plan_basis_items(week, context, current_phase_title),
        },
        "safety_gate": {
            "title": "需要先留意的情况",
            "items": safety_items,
        },
        "current_week_focus": {
            "title": "当前优先级",
            "subtitle": _birth_journey_current_focus_subtitle(week, context),
            "items": current_items,
        },
        "next_7_days": {
            "title": "接下来 7 天行动清单",
            "subtitle": "把当前优先级拆成这周能完成的几个小动作。",
            "context_reason": next_7_context_reason,
            "items": next_7_items,
            "grouped_items": next_7_grouped_items,
        },
        "next_2_4_weeks": {
            "title": "未来 2-4 周",
            "items": next_2_4_weeks,
        },
        "later_milestones": {
            "title": "后续大节点",
            "items": later_milestones,
        },
    }


BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS = 22
BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS = 84
BIRTH_JOURNEY_PLAN_ITEM_STEP_MAX_CHARS = 36
BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS = 96
BIRTH_JOURNEY_PLAN_BASIS_DETAIL_MAX_CHARS = 96
BIRTH_JOURNEY_PRIORITY_ESSENTIAL = "essential"
BIRTH_JOURNEY_PRIORITY_SUPPORTIVE = "supportive"
BIRTH_JOURNEY_PRIORITY_LABELS = {
    BIRTH_JOURNEY_PRIORITY_ESSENTIAL: "优先确认事项",
    BIRTH_JOURNEY_PRIORITY_SUPPORTIVE: "支持性建议",
}
BIRTH_JOURNEY_PRIORITY_TITLE_PREFIXES = {
    BIRTH_JOURNEY_PRIORITY_ESSENTIAL: "【重要】",
    BIRTH_JOURNEY_PRIORITY_SUPPORTIVE: "【建议】",
}
BIRTH_JOURNEY_PRIORITY_KNOWN_TITLE_PREFIXES = tuple(BIRTH_JOURNEY_PRIORITY_TITLE_PREFIXES.values()) + tuple(
    f"【{label}】" for label in BIRTH_JOURNEY_PRIORITY_LABELS.values()
)


def _birth_journey_title_without_priority_prefix(title: Any) -> str:
    text = str(title or "").strip()
    for prefix in BIRTH_JOURNEY_PRIORITY_KNOWN_TITLE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _birth_journey_visible_priority_title(title: Any, priority_type: str) -> str:
    base_title = _birth_journey_title_without_priority_prefix(title)
    prefix = BIRTH_JOURNEY_PRIORITY_TITLE_PREFIXES.get(str(priority_type or "").strip())
    if not prefix:
        return _truncate_birth_journey_plan_text(base_title, BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS)
    max_base_chars = max(1, BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS - len(prefix))
    return prefix + _truncate_birth_journey_plan_text(base_title, max_base_chars)


def _birth_journey_legacy_next_7_item_upgrade(title: str) -> dict[str, Any]:
    upgrades: dict[str, dict[str, Any]] = {
        "确认高龄孕期关注重点": {
            "title": "问清高龄孕期 3 个检查重点",
            "steps": ["问产检频率是否要调整", "问胎儿监测怎么安排", "问分娩方式是否需提前评估"],
            "done_criteria": "已记录产检频率、胎儿监测安排和分娩方式评估口径。",
            "after_done_value": "做完后，你会知道高龄因素具体影响哪些检查和后续安排。",
        },
        "确认高龄孕期监测安排": {
            "title": "下次产检问清高龄监测 3 件事",
            "steps": ["问血压血糖是否要在家记录", "问胎儿监测或复查频率", "问异常时当天联系谁"],
            "done_criteria": "已记录监测频率、异常阈值和医院联系路径。",
            "after_done_value": "做完后，你会知道哪些变化要观察、什么时候复查、异常时找谁。",
        },
        "确认血压血糖监测安排": {
            "title": "问清血压血糖监测规则",
            "steps": ["问在家记录的频率和时间", "问异常阈值和处理方式", "问下次复查或带记录的时间"],
            "done_criteria": "已记录监测频率、异常阈值、复查时间和记录带给谁看。",
            "after_done_value": "做完后，你会知道每天要不要测、测到什么数值要联系医院。",
        },
        "补齐下次产检时间": {
            "title": "今天补齐下次产检日期和项目",
            "steps": ["确认下次产检日期和地点", "记录要做的检查项目", "列出是否空腹和需带材料"],
            "done_criteria": "已记录日期、地点、检查项目、是否空腹和需带材料。",
            "after_done_value": "做完后，你会知道接下来一周围绕哪次产检准备，不会临近才发现漏预约。",
        },
        "整理产检报告里的待确认项": {
            "title": "从产检报告圈出 3 个待确认点",
            "steps": ["圈出报告里异常或没看懂的词", "写下未预约或未复查的项目", "整理成下次产检 3 个问题"],
            "done_criteria": "已形成 3 个下次产检可直接问医生的问题。",
            "after_done_value": "做完后，下次产检会更聚焦，不容易把报告里的疑问带回家。",
        },
        "给生活压力留缓冲": {
            "title": "把最大生活压力拆成 1 个动作",
            "steps": ["写下最影响执行的一件事", "拆出今天 15 分钟能完成的动作", "告诉支持人你需要的一个帮助"],
            "done_criteria": "已选出一个压力点，并完成或安排了一个 15 分钟动作。",
            "after_done_value": "做完后，计划不会停留在担心里，会变成今天能推进的一小步。",
        },
        "拆开最焦虑的三件事": {
            "title": "把焦虑拆成 3 个可处理问题",
            "steps": ["写下最担心的 3 件事", "标出需要问医生的一件", "标出今天能安排的一件"],
            "done_criteria": "已把担心分成医生确认、自己安排、家人支持三类。",
            "after_done_value": "做完后，焦虑会变成可提问、可安排、可求助的清单。",
        },
        "把宝宝情况问题列给医生": {
            "title": "下次产检带上宝宝情况 3 个问题",
            "steps": ["写下最担心的宝宝变化", "问胎儿生长或胎动是否正常", "问是否需要复查或额外观察"],
            "done_criteria": "已得到医生对宝宝情况、复查需求和日常观察方式的答复。",
            "after_done_value": "做完后，你会少靠猜测判断宝宝情况，知道接下来观察什么。",
        },
        "把最担心的问题列成三条": {
            "title": "把担心点整理成 3 个医生问题",
            "steps": ["把担心写成 3 个问句", "标出最想先解决的一条", "把问题保存到下次产检清单"],
            "done_criteria": "已形成 3 个可以直接问医生或家人的具体问题。",
            "after_done_value": "做完后，下一次沟通会更省力，也不容易漏掉真正担心的点。",
        },
    }
    upgrade = upgrades.get(str(title or "").strip())
    if not upgrade:
        return {}
    result = dict(upgrade)
    result["completion_followup"] = str(result.get("after_done_value") or "").strip()
    return result


def _normalize_birth_journey_grouped_next_7_items(
    value: Any,
    all_items: list[dict[str, Any]],
    week: Any,
) -> dict[str, Any]:
    fallback = _birth_journey_grouped_next_7_items(all_items, week, {})
    if not isinstance(value, dict):
        return fallback

    item_by_id = {str(item.get("id") or "").strip(): item for item in all_items if isinstance(item, dict)}
    normalized: dict[str, Any] = {}
    for group_key in (BIRTH_JOURNEY_PRIORITY_ESSENTIAL, BIRTH_JOURNEY_PRIORITY_SUPPORTIVE):
        raw_group = value.get(group_key) if isinstance(value.get(group_key), dict) else {}
        fallback_group = fallback.get(group_key) if isinstance(fallback.get(group_key), dict) else {}
        group_items: list[dict[str, Any]] = []
        raw_items = raw_group.get("items") if isinstance(raw_group.get("items"), list) else []
        if raw_items:
            for raw_item in _normalize_birth_journey_next_7_todo_items(raw_items):
                item_id = str(raw_item.get("id") or "").strip()
                item = item_by_id.get(item_id, raw_item)
                if item.get("priority_type") == group_key:
                    group_items.append(item)
        if not group_items:
            fallback_items = fallback_group.get("items") if isinstance(fallback_group.get("items"), list) else []
            group_items = [item for item in fallback_items if isinstance(item, dict)]
        if not group_items:
            continue
        normalized_group = dict(raw_group or fallback_group)
        normalized_group["title"] = str(raw_group.get("title") or fallback_group.get("title") or "").strip()
        normalized_group["intro"] = str(raw_group.get("intro") or fallback_group.get("intro") or "").strip()
        normalized_group["items"] = group_items
        normalized[group_key] = normalized_group
    return normalized or fallback


def _birth_journey_grouped_next_7_items(items: list[dict[str, Any]], week: Any, context: dict[str, Any]) -> dict[str, Any]:
    essential_items = [
        item
        for item in items
        if isinstance(item, dict) and item.get("priority_type") == BIRTH_JOURNEY_PRIORITY_ESSENTIAL
    ]
    supportive_items = [
        item
        for item in items
        if isinstance(item, dict) and item.get("priority_type") == BIRTH_JOURNEY_PRIORITY_SUPPORTIVE
    ]
    grouped: dict[str, Any] = {}
    if essential_items:
        grouped[BIRTH_JOURNEY_PRIORITY_ESSENTIAL] = {
            "title": "按照你的孕周先确认",
            "intro": _birth_journey_essential_group_intro(week, context),
            "items": essential_items,
        }
    if supportive_items:
        grouped[BIRTH_JOURNEY_PRIORITY_SUPPORTIVE] = {
            "title": "帮助你更稳地推进",
            "intro": _birth_journey_supportive_group_intro(week, context),
            "items": supportive_items,
        }
    return grouped


def _birth_journey_essential_group_intro(week: Any, context: dict[str, Any]) -> str:
    anchors: list[str] = []
    if isinstance(week, int):
        anchors.append(f"你现在孕 {week} 周")
    else:
        anchors.append("你目前提供的信息")
    essential_labels: list[str] = []
    checkup_status = _birth_journey_substantive_text(context.get("checkup_status"))
    if checkup_status:
        essential_labels.append("产检/复查节奏")
    age = _birth_journey_context_age(context)
    if age is not None and age >= 35:
        essential_labels.append("孕期监测安排")
    if _birth_journey_substantive_text(context.get("risk_factors")) or _birth_journey_substantive_text(context.get("medical_notes")):
        essential_labels.append("风险因素或医生提醒")
    fetus_count = _birth_journey_substantive_text(context.get("fetus_count"))
    if any(token in fetus_count for token in ("双", "多", "三")):
        essential_labels.append("多胎产检节奏")
    if _birth_journey_substantive_text(context.get("birth_path")) or _birth_journey_substantive_text(context.get("birth_setting")):
        essential_labels.append("分娩/入院安排")
    if essential_labels:
        anchors.append("以及" + _birth_journey_join_concern_labels(_unique_text_list(essential_labels, 3)))
    else:
        anchors.append("当前阶段的关键准备窗口")
    return "按照" + "，".join(anchors) + "，接下来 7 天优先把这些会影响后续安排的事先确认掉："


def _birth_journey_supportive_group_intro(week: Any, context: dict[str, Any]) -> str:
    labels = _birth_journey_context_concern_labels(context, 2)
    if _birth_journey_substantive_text(context.get("lifestyle_context")):
        labels.append("生活/工作执行压力")
    if _birth_journey_support_text(context.get("support_person")):
        labels.append("支持人分工")
    if _birth_journey_substantive_text(context.get("feeding_intention")) or _birth_journey_substantive_text(context.get("feeding_ibclc_context")):
        labels.append("喂养准备")
    labels = _unique_text_list(labels, 3)
    if labels:
        return "另外，针对你提到的" + _birth_journey_join_concern_labels(labels) + "，建议这周也做几件能让推进更稳的事："
    if isinstance(week, int):
        return f"另外，为了让孕 {week} 周后的准备不堆到临近生产时才处理，建议这周顺手推进："
    return "另外，为了让接下来的准备不堆到临近生产时才处理，建议这周顺手推进："


def _birth_journey_plan_basis_items(week: Any, context: dict[str, Any], current_phase_title: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(week, int):
        if week < 14:
            detail = f"你现在孕 {week} 周，计划先围绕首次产检、建档和早孕筛查闭环来排。"
        elif week < 24:
            detail = f"你现在孕 {week} 周，计划先把常规产检、大排畸和生产支持准备接起来。"
        elif week < 29:
            detail = f"你现在孕 {week} 周，计划先围绕糖耐、产检复查和接下来几周的安排来排。"
        elif week < 35:
            detail = f"你现在孕 {week} 周，计划先把胎动观察、产检节奏和入院准备接起来。"
        else:
            detail = f"你现在孕 {week} 周，计划优先收口入院信号、证件材料和陪产分工。"
        items.append(_birth_journey_basis_item("当前孕周", detail, ["current_week"]))
    elif current_phase_title:
        items.append(
            _birth_journey_basis_item(
                "当前阶段",
                f"孕周还不够精确，先按{current_phase_title}常见准备顺序安排，再等你补充孕周后细化。",
                ["current_phase"],
            )
        )

    concern_label = _birth_journey_join_concern_labels(_birth_journey_context_concern_labels(context, 3))
    if concern_label:
        items.append(
            _birth_journey_basis_item(
                "你提到的担心",
                f"你提到{concern_label}，所以计划会先把模糊压力拆成医生能确认、自己能安排、家人能支持的动作。",
                ["entry_reason", "initial_concerns", "entry_concern_followup", "top_worries"],
            )
        )

    checkup_status = _birth_journey_substantive_text(context.get("checkup_status"))
    if checkup_status:
        items.append(
            _birth_journey_basis_item(
                "产检状态",
                "你已经提供产检状态，所以计划优先把报告、复查和下次要问医生的问题串起来。",
                ["checkup_status"],
            )
        )

    age = _birth_journey_context_age(context)
    risk_text = _birth_journey_substantive_text(context.get("risk_factors"))
    medical_notes = _birth_journey_substantive_text(context.get("medical_notes"))
    fetus_count = _birth_journey_substantive_text(context.get("fetus_count"))
    if age is not None and age >= 35:
        items.append(_birth_journey_basis_item("年龄因素", f"你是 {age} 岁，计划会把产检频率、胎儿监测和分娩方式确认提前。", ["age"]))
    if risk_text or medical_notes:
        items.append(_birth_journey_basis_item("风险和医生提醒", "你提到风险因素或医生提醒，计划会优先确认监测频率、复查指标和何时联系医院。", ["risk_factors", "medical_notes"]))
    if any(token in fetus_count for token in ("双", "多", "三")):
        items.append(_birth_journey_basis_item("多胎情况", "你是多胎，计划会按更谨慎的产检和入院节奏来安排。", ["fetus_count"]))

    birth_path = _birth_journey_substantive_text(context.get("birth_path"))
    birth_setting = _birth_journey_substantive_text(context.get("birth_setting"))
    support = _birth_journey_support_text(context.get("support_person"))
    feeding = _birth_journey_substantive_text(context.get("feeding_intention")) or _birth_journey_substantive_text(context.get("feeding_ibclc_context"))
    lifestyle = _birth_journey_substantive_text(context.get("lifestyle_context"))
    if birth_path or birth_setting:
        items.append(_birth_journey_basis_item("生产安排", "你提供了分娩方式或医院信息，计划会提前落到入院流程、陪产探视和分娩沟通。", ["birth_path", "birth_setting"]))
    if support:
        items.append(_birth_journey_basis_item("支持人分工", f"你提到{support}，计划会把临产和产后支持拆成可以分给支持人的事项。", ["support_person"]))
    if feeding:
        items.append(_birth_journey_basis_item("喂养准备", "你提供了喂养相关信息，计划会提前放入住院后 48 小时喂养支持和 IBCLC 问题。", ["feeding_intention", "feeding_ibclc_context"]))
    if lifestyle:
        items.append(_birth_journey_basis_item("生活执行压力", "你提到生活或工作场景，计划会把建议拆成这周能完成的小动作，减少执行负担。", ["lifestyle_context"]))
    return _unique_birth_journey_basis_items(items)[:4]


def _birth_journey_basis_item(title: str, detail: str, based_on: list[str]) -> dict[str, Any]:
    return {
        "title": _truncate_birth_journey_plan_text(title, BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS),
        "detail": _truncate_birth_journey_plan_text(detail, BIRTH_JOURNEY_PLAN_BASIS_DETAIL_MAX_CHARS),
        "based_on": based_on,
    }


def _unique_birth_journey_basis_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        title = str(item.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        unique_items.append(item)
    return unique_items


def _birth_journey_plan_item(
    title: str,
    reason: str,
    timeframe: str,
    based_on: list[str] | None = None,
    *,
    steps: list[str] | None = None,
    done_criteria: str | None = None,
    after_done_value: str | None = None,
) -> dict[str, Any]:
    clean_title = _truncate_birth_journey_plan_text(title, BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS)
    clean_reason = _truncate_birth_journey_plan_text(
        _birth_journey_plan_reason_text(reason),
        BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS,
    )
    basis = based_on or []
    clean_steps = [
        _truncate_birth_journey_plan_text(step, BIRTH_JOURNEY_PLAN_ITEM_STEP_MAX_CHARS)
        for step in (steps or _birth_journey_plan_item_steps(title, basis))[:3]
        if str(step or "").strip()
    ]
    clean_done_criteria = _truncate_birth_journey_plan_text(
        done_criteria or _birth_journey_done_criteria(title),
        BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS,
    )
    clean_after_done_value = _truncate_birth_journey_plan_text(
        after_done_value or _birth_journey_after_done_value(title, basis),
        BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS,
    )
    priority_type, priority_label = _birth_journey_plan_item_priority(title, basis)
    return {
        "title": clean_title,
        "reason": clean_reason,
        "timeframe": timeframe,
        "based_on": basis,
        "priority_type": priority_type,
        "priority_label": priority_label,
        "why_for_you": clean_reason,
        "steps": clean_steps,
        "done_criteria": clean_done_criteria,
        "after_done_value": clean_after_done_value,
        "completion_followup": _birth_journey_completion_followup(title, clean_after_done_value),
    }


def _birth_journey_plan_item_priority(title: str, based_on: list[str]) -> tuple[str, str]:
    text = str(title or "")
    essential_keys = {
        "current_week",
        "checkup_status",
        "checkup_window",
        "current_symptoms",
        "risk_factors",
        "medical_notes",
        "age",
        "fetus_count",
        "birth_path",
        "birth_setting",
        "milestone",
    }
    supportive_keys = {
        "entry_reason",
        "initial_concerns",
        "entry_concern_followup",
        "top_worries",
        "lifestyle_context",
        "support_person",
        "feeding_intention",
        "feeding_ibclc_context",
    }
    essential_tokens = (
        "产检",
        "糖耐",
        "NT",
        "早筛",
        "大排畸",
        "复查",
        "报告",
        "医生",
        "监测",
        "高龄",
        "血压",
        "血糖",
        "胎动",
        "风险",
        "医院",
        "入院",
        "证件",
        "待产",
        "胎位",
        "生长",
        "剖宫产",
        "顺产",
        "多胎",
    )
    supportive_tokens = (
        "焦虑",
        "担心",
        "担忧",
        "生活",
        "睡眠",
        "通勤",
        "久坐",
        "久站",
        "压力",
        "休息",
        "支持",
        "分工",
        "家人",
        "伴侣",
        "喂养",
        "母乳",
        "混合",
        "泵奶",
        "背奶",
        "IBCLC",
    )
    if any(key in essential_keys for key in based_on) or any(token in text for token in essential_tokens):
        priority_type = BIRTH_JOURNEY_PRIORITY_ESSENTIAL
    elif any(key in supportive_keys for key in based_on) or any(token in text for token in supportive_tokens):
        priority_type = BIRTH_JOURNEY_PRIORITY_SUPPORTIVE
    else:
        priority_type = BIRTH_JOURNEY_PRIORITY_ESSENTIAL
    return priority_type, BIRTH_JOURNEY_PRIORITY_LABELS[priority_type]


def _birth_journey_plan_item_steps(title: str, based_on: list[str]) -> list[str]:
    text = str(title or "")
    steps: list[str]
    if "糖耐" in text:
        steps = ["确认糖耐预约日期和地点", "记下禁食开始时间和抽血流程", "约好报告回看或复查时间"]
    elif any(token in text for token in ("产检", "报告", "复查", "医生", "风险", "监测", "高龄", "血压", "血糖")):
        steps = ["把相关报告或医生备注放到一起", "列出最想确认的 3 个问题", "下次产检时逐条问清"]
    elif any(token in text for token in ("胎动", "水肿", "观察")):
        steps = ["选一个每天固定观察时段", "记录胎动和明显不适变化", "保存异常时联系医院的步骤"]
    elif any(token in text for token in ("医院", "入院", "证件", "待产", "入口", "流程")) and not any(token in text for token in ("剖", "顺产", "分娩")):
        steps = ["确认医院入口、证件和预登记要求", "把联系号码和路线存到手机", "和陪同人同步出发规则"]
    elif any(token in text for token in ("支持", "分工", "家人", "伴侣")):
        steps = ["写下需要别人负责的事项", "明确谁负责联系医院和拿材料", "把分工发给支持人确认"]
    elif any(token in text for token in ("喂养", "母乳", "混合", "泵奶", "背奶", "IBCLC", "含乳", "涨奶")):
        steps = ["写下喂养偏好和担心点", "确认医院是否有护士或 IBCLC 支持", "列出产后 48 小时要问的问题"]
    elif any(token in text for token in ("剖宫产", "剖")):
        steps = ["问清术前禁食和入院时间", "确认术后下床和伤口观察口径", "把住院照护分工写下来"]
    elif any(token in text for token in ("顺产", "宫缩", "破水", "见红", "分娩")):
        steps = ["问清宫缩、破水、见红后的联系口径", "确认镇痛和陪产规则", "把沟通偏好发给陪同人"]
    elif any(token in text for token in ("生活", "睡眠", "通勤", "久坐", "久站", "压力", "休息")):
        steps = ["选一个最影响执行的生活压力点", "拆成今天能调整的小动作", "告诉支持人你需要的具体帮助"]
    elif any(token in text for token in ("焦虑", "担心", "担忧")) or any(key in based_on for key in ("entry_reason", "top_worries", "entry_concern_followup")):
        steps = ["把担心写成 3 条", "标出哪些要问医生、哪些要安排", "把需要家人支持的事单独列出"]
    else:
        steps = ["写下这项要确认的具体问题", "确定要问谁或在哪里查", "把结果记录到计划里"]
    return [_truncate_birth_journey_plan_text(step, BIRTH_JOURNEY_PLAN_ITEM_STEP_MAX_CHARS) for step in steps[:3]]


def _birth_journey_done_criteria(title: str) -> str:
    text = str(title or "")
    if "糖耐" in text:
        criteria = "已知道糖耐日期、禁食时间、抽血流程和报告回看方式。"
    elif any(token in text for token in ("产检", "报告", "复查", "医生", "风险", "监测", "高龄", "血压", "血糖")):
        criteria = "已形成下次产检可直接问医生的问题清单。"
    elif any(token in text for token in ("胎动", "水肿", "观察")):
        criteria = "已固定观察时段，并写清异常时联系医院的步骤。"
    elif any(token in text for token in ("医院", "入院", "证件", "待产", "入口", "流程")) and not any(token in text for token in ("剖", "顺产", "分娩")):
        criteria = "已确认入院入口、证件材料、陪产探视和联系路径。"
    elif any(token in text for token in ("支持", "分工", "家人", "伴侣")):
        criteria = "已写清谁负责联系医院、拿材料、出发和记录医嘱。"
    elif any(token in text for token in ("喂养", "母乳", "混合", "泵奶", "背奶", "IBCLC", "含乳", "涨奶")):
        criteria = "已列出住院前要确认的喂养支持问题。"
    elif any(token in text for token in ("焦虑", "担心", "担忧")):
        criteria = "已把担心拆成医生确认、自己安排和家人支持三类。"
    else:
        criteria = "已知道下一步要问谁、什么时候做、做到什么算完成。"
    return _truncate_birth_journey_plan_text(criteria, BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS)


def _birth_journey_after_done_value(title: str, based_on: list[str]) -> str:
    text = str(title or "")
    if "糖耐" in text:
        value = "做完后，我可以继续帮你整理糖耐当天流程和结果回看问题。"
    elif any(token in text for token in ("产检", "报告", "复查", "医生", "风险", "监测", "高龄", "血压", "血糖")):
        value = "做完后，我可以继续帮你整理下次产检要问医生的 3-5 个问题。"
    elif any(token in text for token in ("胎动", "水肿", "观察")):
        value = "做完后，我可以帮你整理一张异常情况联系卡。"
    elif any(token in text for token in ("医院", "入院", "证件", "待产", "入口", "流程")) and not any(token in text for token in ("剖", "顺产", "分娩")):
        value = "做完后，我可以继续帮你整理入院流程确认清单或待产包。"
    elif any(token in text for token in ("支持", "分工", "家人", "伴侣")):
        value = "做完后，我可以帮你整理临产支持人分工清单。"
    elif any(token in text for token in ("喂养", "母乳", "混合", "泵奶", "背奶", "IBCLC", "含乳", "涨奶")):
        value = "做完后，我可以帮你整理住院后 48 小时喂养和 IBCLC 求助问题。"
    elif any(token in text for token in ("剖宫产", "剖", "顺产", "宫缩", "破水", "见红", "分娩")):
        value = "做完后，我可以继续帮你整理分娩沟通单。"
    elif any(token in text for token in ("焦虑", "担心", "担忧")) or any(key in based_on for key in ("entry_reason", "top_worries", "entry_concern_followup")):
        value = "做完后，我可以把这些担心继续拆成医生问题、自己安排和家人分工。"
    else:
        value = "做完后，我可以继续帮你把这一项拆成更细的执行清单。"
    return _truncate_birth_journey_plan_text(value, BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS)


def _birth_journey_completion_followup(title: str, after_done_value: str) -> str:
    value = str(after_done_value or "").strip()
    if value:
        return value
    return _truncate_birth_journey_plan_text(f"完成“{title}”后，我可以继续帮你细化下一步。", BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS)


def _birth_journey_plan_reason_text(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    if text.startswith(("考虑到", "目的是", "为了")):
        return text
    action_starters = (
        "先",
        "把",
        "确认",
        "问清",
        "定好",
        "整理",
        "固定",
        "选",
        "写清",
        "聚焦",
        "重点",
        "明确",
        "补齐",
        "拆成",
        "分开",
        "落到",
    )
    if text.startswith(action_starters):
        return f"目的是{text}"
    return f"目的是让你知道：{text}"


def _truncate_birth_journey_plan_text(value: str, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip("，。；、,. ") + "…"


def _unique_birth_journey_plan_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        unique_items.append(item)
    return unique_items


def _birth_journey_substantive_text(value: Any) -> str:
    text = _first_answer_text(value)
    if not text:
        return ""
    lowered = _normalized_placeholder(text)
    if lowered in PLACEHOLDER_VALUES:
        return ""
    negative_values = {
        "没有",
        "无",
        "暂无",
        "暂时没有",
        "不用",
        "不需要",
        "正常",
        "没什么",
        "没有特殊情况",
        "没有高危",
        "没有风险",
        "没有不舒服",
        "没有异常",
        "未上传产检记录",
        "没有上传产检记录",
        "没有产检记录",
        "跳过产检记录",
        "跳过上传产检记录",
        "暂不上传产检记录",
    }
    if lowered in negative_values:
        return ""
    return text


def _birth_journey_support_text(value: Any) -> str:
    text = _birth_journey_substantive_text(value)
    if not text:
        return ""
    unsupported_tokens = ("自己", "没人", "支持少", "暂时没有", "不需要")
    if any(token in text for token in unsupported_tokens):
        return ""
    return text


def _birth_journey_context_concern_text(context: dict[str, Any]) -> str:
    return "；".join(
        _unique_text_list(
            [
                context.get("entry_concern_followup"),
                context.get("top_worries"),
                context.get("initial_concerns"),
                context.get("entry_reason"),
                context.get("lifestyle_context"),
            ],
            6,
        )
    )


def _birth_journey_context_concern_labels(context: dict[str, Any], max_labels: int = 3) -> list[str]:
    text = _birth_journey_context_concern_text(context)
    if not text:
        return []
    labels: list[str] = []
    emotion_label = _birth_journey_explicit_emotion_label(text)
    if emotion_label:
        labels.append(emotion_label)
    elif _birth_journey_text_has_explicit_worry(text):
        labels.append("担心点")
    if any(token in text for token in ("血压", "血糖", "监测", "糖耐", "高血压", "糖尿病")):
        labels.append("血压血糖监测")
    if any(token in text for token in ("宝宝", "胎儿", "胎动", "发育", "生长")):
        labels.append("宝宝情况")
    if any(token in text for token in ("产检", "复查", "检查", "报告")):
        labels.append("产检复查")
    if any(token in text for token in ("准备", "安排", "不知道", "怎么办", "先做什么")):
        labels.append("后续安排")
    if not labels:
        label = text.replace("我主要是", "").replace("我主要", "").strip("，。；、 ")
        if label:
            labels.append(_truncate_birth_journey_plan_text(label, 18))
    return _unique_text_list(labels, max_labels)


def _birth_journey_join_concern_labels(labels: list[str]) -> str:
    clean_labels = _unique_text_list(labels, 3)
    if len(clean_labels) <= 1:
        return clean_labels[0] if clean_labels else ""
    return "、".join(clean_labels[:-1]) + "和" + clean_labels[-1]


def _birth_journey_next_7_context_reason(week: Any, context: dict[str, Any]) -> str:
    parts: list[str] = []
    age = _birth_journey_context_age(context)
    if age is not None and age >= 35:
        parts.append(f"{age} 岁")
    if isinstance(week, int):
        parts.append(f"孕 {week} 周")
    concern_label = _birth_journey_join_concern_labels(_birth_journey_context_concern_labels(context, 2))
    if concern_label:
        parts.append(f"提到{concern_label}")
    if parts:
        spacer = "" if parts[0].startswith("提到") else " "
        return "考虑到你" + spacer + "、".join(parts)
    return "结合你当前孕周和已提供的信息"


def _birth_journey_personalized_next_7_items(week: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    age = _birth_journey_context_age(context)
    concern_text = _birth_journey_context_concern_text(context)
    explicit_concern_text = "；".join(
        _unique_text_list(
            [
                context.get("entry_concern_followup"),
                context.get("top_worries"),
                context.get("initial_concerns"),
                context.get("entry_reason"),
            ],
            5,
        )
    )
    emotion_label = _birth_journey_explicit_emotion_label(concern_text)
    has_monitoring_concern = any(token in concern_text for token in ("血压", "血糖", "监测", "糖耐", "高血压", "糖尿病"))
    has_baby_concern = any(token in concern_text for token in ("宝宝", "胎儿", "胎动", "发育", "生长"))

    if emotion_label:
        is_anxiety = emotion_label == "焦虑"
        items.append(
            _birth_journey_plan_item(
                "把焦虑拆成 3 个可处理问题" if is_anxiety else "把压力点拆成 3 个可处理问题",
                (
                    "考虑到你明确提到焦虑，目的是把担心分成能问医生、能安排和需要家人支持的事项。"
                    if is_anxiety
                    else f"考虑到你表达了{emotion_label}，目的是把压力分成能问医生、能安排和需要家人支持的事项。"
                ),
                "今天",
                ["entry_reason", "top_worries", "entry_concern_followup"],
                steps=["写下最担心的 3 件事", "标出需要问医生的一件", "标出今天能安排的一件"],
                done_criteria="已把担心分成医生确认、自己安排、家人支持三类。",
                after_done_value=(
                    "做完后，焦虑会变成可提问、可安排、可求助的清单。"
                    if is_anxiety
                    else "做完后，压力点会变成可提问、可安排、可求助的清单。"
                ),
            )
        )
    if age is not None and age >= 35 and has_monitoring_concern:
        items.append(
            _birth_journey_plan_item(
                "下次产检问清高龄监测 3 件事",
                f"考虑到你 {age} 岁且提到监测，目的是问清血压血糖、复查频率和异常时联系谁。",
                "下次产检前",
                ["age", "entry_concern_followup"],
                steps=["问血压血糖是否要在家记录", "问胎儿监测或复查频率", "问异常时当天联系谁"],
                done_criteria="已记录监测频率、异常阈值和医院联系路径。",
                after_done_value="做完后，你会知道哪些变化要观察、什么时候复查、异常时找谁。",
            )
        )
    elif age is not None and age >= 35:
        items.append(
            _birth_journey_plan_item(
                "问清高龄孕期 3 个检查重点",
                f"考虑到你 {age} 岁，目的是问清产检频率、胎儿监测和分娩方式是否需要特别安排。",
                "下次产检前",
                ["age", "entry_reason"],
                steps=["问产检频率是否要调整", "问胎儿监测怎么安排", "问分娩方式是否需提前评估"],
                done_criteria="已记录产检频率、胎儿监测安排和分娩方式评估口径。",
                after_done_value="做完后，你会知道高龄因素具体影响哪些检查和后续安排。",
            )
        )
    elif has_monitoring_concern:
        items.append(
            _birth_journey_plan_item(
                "问清血压血糖监测规则",
                "考虑到你提到监测，目的是问清记录频率、异常阈值和复查节点。",
                "下次产检前",
                ["entry_concern_followup"],
                steps=["问在家记录的频率和时间", "问异常阈值和处理方式", "问下次复查或带记录的时间"],
                done_criteria="已记录监测频率、异常阈值、复查时间和记录带给谁看。",
                after_done_value="做完后，你会知道每天要不要测、测到什么数值要联系医院。",
            )
        )
    if has_baby_concern:
        items.append(
            _birth_journey_plan_item(
                "下次产检带上宝宝情况 3 个问题",
                "考虑到你担心宝宝情况，目的是把胎动、胎儿生长和需要复查的点一次问清楚。",
                "下次产检前",
                ["entry_concern_followup", "top_worries"],
                steps=["写下最担心的宝宝变化", "问胎儿生长或胎动是否正常", "问是否需要复查或额外观察"],
                done_criteria="已得到医生对宝宝情况、复查需求和日常观察方式的答复。",
                after_done_value="做完后，你会少靠猜测判断宝宝情况，知道接下来观察什么。",
            )
        )
    if not items and explicit_concern_text:
        items.append(
            _birth_journey_plan_item(
                "把担心点整理成 3 个医生问题",
                "考虑到你已经说出担心点，目的是把模糊压力变成医生能回答、自己能安排的具体问题。",
                "今天",
                ["entry_reason", "top_worries"],
                steps=["把担心写成 3 个问句", "标出最想先解决的一条", "把问题保存到下次产检清单"],
                done_criteria="已形成 3 个可以直接问医生或家人的具体问题。",
                after_done_value="做完后，下一次沟通会更省力，也不容易漏掉真正担心的点。",
            )
        )
    return _unique_birth_journey_plan_items(items)[:3]


def _birth_journey_current_focus_subtitle(week: Any, context: dict[str, Any]) -> str:
    week_text = f"你现在是孕 {week} 周" if isinstance(week, int) else "先按你目前提供的信息安排"
    if context.get("checkup_status"):
        return f"{week_text}，先抓最影响后续准备和安全感的几件事。"
    return f"{week_text}，先抓最影响后续准备和安全感的几件事。"


def _birth_journey_safety_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    symptoms_text = "、".join(_text_list(context.get("current_symptoms")))
    if not symptoms_text:
        return []
    items: list[dict[str, Any]] = []
    if any(token in symptoms_text for token in ("出血", "流血", "流水", "破水", "胎动", "腹痛", "头痛", "视物", "发热", "胸痛", "气短")):
        items.append(
            _birth_journey_plan_item(
                "先确认是否需要联系医院或医生",
                "这类变化需要先按医院口径判断，不要当作普通准备事项处理。",
                "现在",
                ["current_symptoms"],
            )
        )
    return items


def _birth_journey_current_focus_items(week: Any, context: dict[str, Any], current_phase: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    checkup_status = _birth_journey_substantive_text(context.get("checkup_status"))
    risk_text = _birth_journey_substantive_text(context.get("risk_factors"))
    medical_notes = _birth_journey_substantive_text(context.get("medical_notes"))
    lifestyle_text = _birth_journey_substantive_text(context.get("lifestyle_context"))
    birth_path = _birth_journey_substantive_text(context.get("birth_path"))
    fetus_count = _birth_journey_substantive_text(context.get("fetus_count"))
    if isinstance(week, int):
        if week < 12:
            items.append(_birth_journey_plan_item("确认首次产检或建档", "先问清预约入口、证件材料、既往检查和用药补剂怎么带。", "本周", ["current_week"]))
        elif 11 <= week <= 14:
            items.append(_birth_journey_plan_item("锁定 NT 或早孕筛查窗口", "确认检查日期、当天准备、报告领取和异常结果联系路径。", "本周", ["current_week"]))
        elif 15 <= week < 18:
            items.append(_birth_journey_plan_item("接上下次产检和筛查节奏", "把检查结果、下次产检和大排畸预约时间串起来。", "本周", ["current_week"]))
        elif 18 <= week <= 22:
            items.append(_birth_journey_plan_item("确认大排畸安排", "问清预约时间、当天流程、是否陪同，以及需要复查时怎么处理。", "本周", ["current_week"]))
        elif 23 <= week < 24:
            items.append(_birth_journey_plan_item("提前排好糖耐窗口", "先确认糖耐预约、抽血流程和当天饮食安排。", "本周", ["current_week", "checkup_window"]))
        elif 24 <= week <= 28:
            items.append(_birth_journey_plan_item("确认糖耐和 24-28 周产检", "问清禁食时长、抽血流程、结果回看和复查时间。", "今天或明天", ["current_week", "checkup_window"]))
        elif 29 <= week < 32:
            items.append(_birth_journey_plan_item("建立胎动、血压和水肿观察节奏", "固定每天观察时间，写清哪些情况要联系医院。", "每天", ["current_week"]))
        elif 32 <= week < 35:
            items.append(_birth_journey_plan_item("确认胎位、生长评估和医院流程", "重点问胎位、生长情况、入院材料、陪产探视和夜间入口。", "本周", ["current_week", "birth_setting"]))
        else:
            items.append(_birth_journey_plan_item("收口入院信号和陪产分工", "确认何时联系医院、证件放哪里、谁负责出发和沟通。", "本周", ["current_week", "support_person"]))
    if checkup_status:
        items.append(_birth_journey_plan_item("整理产检信息和复查问题", "把已做检查、异常提示、未预约项和要问医生的问题分开。", "下次产检前", ["checkup_status"]))
    if risk_text or medical_notes:
        items.append(_birth_journey_plan_item("把风险因素列成医生问题", "重点确认监测频率、复查指标、何时就医和分娩方式影响。", "下次产检前", ["risk_factors", "medical_notes"]))
    if "剖" in birth_path:
        items.append(_birth_journey_plan_item("确认剖宫产相关流程", "问清禁食、入院时间、住院天数、下床和伤口护理。", "下次产检", ["birth_path"]))
    elif any(token in birth_path for token in ("顺", "阴道")):
        items.append(_birth_journey_plan_item("确认顺产待产沟通点", "问清宫缩、破水、见红后的联系口径，以及镇痛和陪产规则。", "下次产检", ["birth_path"]))
    if any(token in fetus_count for token in ("双", "多", "三")):
        items.append(_birth_journey_plan_item("按多胎节奏确认产检窗口", "确认复查频率、早产风险提示和医院建议的入院时机。", "下次产检", ["fetus_count"]))
    if lifestyle_text:
        items.append(_birth_journey_plan_item("把生活限制转成日程安排", "把久坐/久站、休息、补剂和求助对象落到每天安排里。", "本周", ["lifestyle_context"]))
    if not items:
        goal = _clean_birth_journey_fragment(current_phase.get("goal"))
        action = _first_birth_journey_item(current_phase.get("actions"))
        items.append(_birth_journey_plan_item(action or "补齐产检和生产准备信息", goal or "先补齐孕周、下次产检、当前不适和医院流程。", "本周", ["current_phase"]))
    return items


def _birth_journey_lifestyle_next_7_item(lifestyle_text: str) -> dict[str, Any]:
    if any(token in lifestyle_text for token in ("通勤", "路上", "坐车", "开车")):
        return _birth_journey_plan_item(
            "固定通勤后的 20 分钟休息",
            "考虑到你提到通勤压力，先给每天最容易透支的时段留出恢复窗口。",
            "从今天开始",
            ["lifestyle_context"],
            steps=["选定到家或到办公室后的休息时段", "把这段时间加到日历或提醒", "告诉支持人这段不安排杂事"],
            done_criteria="已设好本周至少 3 天的通勤后休息提醒。",
            after_done_value="做完后，通勤不会直接挤掉休息和产检准备时间。",
        )
    if any(token in lifestyle_text for token in ("久坐", "坐着", "上班", "办公")):
        return _birth_journey_plan_item(
            "设置久坐后的起身提醒",
            "考虑到你提到久坐或上班场景，先把身体不适风险拆成可执行的小提醒。",
            "从今天开始",
            ["lifestyle_context"],
            steps=["选一个 45-60 分钟提醒间隔", "设置起身喝水或走动提醒", "记录腰酸腿抽筋是否减少"],
            done_criteria="已设置提醒，并试运行至少 1 个工作日。",
            after_done_value="做完后，你会更容易发现久坐和不适之间的关系，也更好和医生描述。",
        )
    if any(token in lifestyle_text for token in ("睡眠", "失眠", "熬夜", "夜醒", "睡不好")):
        return _birth_journey_plan_item(
            "今晚固定睡前 30 分钟降噪",
            "考虑到你提到睡眠压力，先把今晚能执行的休息动作定下来。",
            "今晚",
            ["lifestyle_context"],
            steps=["睡前 30 分钟停掉工作消息", "把明天要做的事写成 3 条", "记录今晚入睡和夜醒情况"],
            done_criteria="已完成一次睡前降噪，并记录睡眠变化。",
            after_done_value="做完后，你能判断哪些安排真的影响睡眠，后续计划会更好调。",
        )
    if any(token in lifestyle_text for token in ("久站", "站着", "站立")):
        return _birth_journey_plan_item(
            "安排久站后的坐下休息点",
            "考虑到你提到久站压力，先把休息点和替换人提前安排好。",
            "从今天开始",
            ["lifestyle_context"],
            steps=["找出今天最容易久站的时段", "安排一个坐下或垫脚休息点", "请支持人帮忙替换 1 次"],
            done_criteria="已给本周最容易久站的时段安排休息点或替换人。",
            after_done_value="做完后，你不用临时硬撑，也更容易判断身体不适是否需要问医生。",
        )
    return _birth_journey_plan_item(
        "把最大生活压力拆成 1 个动作",
        "考虑到你提到生活或工作压力，先把最影响执行的一件事拆到今天能做。",
        "今天",
        ["lifestyle_context"],
        steps=["写下最影响执行的一件事", "拆出今天 15 分钟能完成的动作", "告诉支持人你需要的一个帮助"],
        done_criteria="已选出一个压力点，并完成或安排了一个 15 分钟动作。",
        after_done_value="做完后，计划不会停留在担心里，会变成今天能推进的一小步。",
    )


def _birth_journey_next_7_day_items(week: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    checkup_status = _birth_journey_substantive_text(context.get("checkup_status"))
    risk_text = _birth_journey_substantive_text(context.get("risk_factors"))
    medical_notes = _birth_journey_substantive_text(context.get("medical_notes"))
    lifestyle_text = _birth_journey_substantive_text(context.get("lifestyle_context"))
    support = _birth_journey_support_text(context.get("support_person"))
    feeding = _birth_journey_substantive_text(context.get("feeding_intention")) or _birth_journey_substantive_text(context.get("feeding_ibclc_context"))
    birth_setting = _birth_journey_substantive_text(context.get("birth_setting"))
    birth_path = _birth_journey_substantive_text(context.get("birth_path"))
    items.extend(_birth_journey_personalized_next_7_items(week, context))
    if checkup_status:
        items.append(
            _birth_journey_plan_item(
                "从产检报告圈出 3 个待确认点",
                "分开记录已做检查、异常提示、未预约项和医生备注，避免下次产检漏问。",
                "未来 7 天",
                ["checkup_status"],
                steps=["圈出报告里异常或没看懂的词", "写下未预约或未复查的项目", "整理成下次产检 3 个问题"],
                done_criteria="已形成 3 个下次产检可直接问医生的问题。",
                after_done_value="做完后，下次产检会更聚焦，不容易把报告里的疑问带回家。",
            )
        )
    else:
        items.append(
            _birth_journey_plan_item(
                "今天补齐下次产检日期和项目",
                "确认预约日期、检查项目和需要带的材料，先把最基础的时间锚点补上。",
                "未来 7 天",
                ["checkup_status"],
                steps=["确认下次产检日期和地点", "记录要做的检查项目", "列出是否空腹和需带材料"],
                done_criteria="已记录日期、地点、检查项目、是否空腹和需带材料。",
                after_done_value="做完后，你会知道接下来一周围绕哪次产检准备，不会临近才发现漏预约。",
            )
        )
    if isinstance(week, int):
        if week < 12:
            items.append(
                _birth_journey_plan_item(
                    "准备首次产检资料清单",
                    "整理末次月经、既往病史、用药补剂和早孕检查，让首次产检少漏信息。",
                    "本周内",
                    ["current_week"],
                    steps=["写下末次月经和既往病史", "整理用药补剂和早孕检查", "确认建档或首次产检材料"],
                    done_criteria="已把首次产检要带的信息和材料整理在同一处。",
                    after_done_value="做完后，首次产检时医生能更快了解你的基础情况。",
                )
            )
        elif 11 <= week <= 14:
            items.append(
                _birth_journey_plan_item(
                    "核对 NT/早筛时间和报告回看",
                    "确认检查准备、报告领取和异常结果联系路径。",
                    "本周内",
                    ["current_week"],
                    steps=["确认检查日期、地点和当天准备", "问清报告领取时间", "保存异常结果联系路径"],
                    done_criteria="已记录检查安排、报告回看时间和异常联系路径。",
                    after_done_value="做完后，NT/早筛不会只停在预约上，后续报告也能接得上。",
                )
            )
        elif 18 <= week <= 22:
            items.append(
                _birth_journey_plan_item(
                    "确认大排畸地点流程和复查方式",
                    "问清地点、时长、陪同要求和复查方式。",
                    "本周内",
                    ["current_week"],
                    steps=["确认检查地点和预计时长", "问是否允许陪同", "问需要复查时怎么预约"],
                    done_criteria="已记录地点、流程、陪同规则和复查方式。",
                    after_done_value="做完后，大排畸当天安排会更可控，复查也知道怎么接上。",
                )
            )
        elif 23 <= week <= 28:
            items.append(
                _birth_journey_plan_item(
                    "排好糖耐禁食抽血和返程",
                    "定好禁食时间、抽血流程、检查后第一餐和返程。",
                    "本周内",
                    ["current_week"],
                    steps=["确认禁食开始时间", "问清抽血流程和耗时", "安排检查后第一餐和返程"],
                    done_criteria="已记录禁食时间、抽血流程、检查后进食和返程安排。",
                    after_done_value="做完后，糖耐当天不容易因为空腹、等待或返程安排临时慌乱。",
                )
            )
        elif week >= 29:
            items.append(
                _birth_journey_plan_item(
                    "固定胎动记录和异常联系规则",
                    "选固定时段记录，并写清异常时联系医院的步骤。",
                    "每天",
                    ["current_week", "current_symptoms"],
                    steps=["选一个每天固定观察时段", "记录胎动和明显不适变化", "保存异常时联系医院的步骤"],
                    done_criteria="已固定观察时段，并写清异常时联系医院的步骤。",
                    after_done_value="做完后，你会有连续记录，异常时也知道先联系哪里。",
                )
            )
    if risk_text or medical_notes:
        items.append(
            _birth_journey_plan_item(
                "下次产检问清风险指标处理口径",
                "聚焦复查指标、活动限制、就医时机和分娩方式影响。",
                "下次产检前",
                ["risk_factors", "medical_notes"],
                steps=["写下医生提过的风险或指标", "问复查频率和异常阈值", "问是否影响活动或分娩方式"],
                done_criteria="已记录复查指标、异常阈值、活动限制和分娩方式影响。",
                after_done_value="做完后，风险因素会变成明确观察规则，而不是一直悬着的担心。",
            )
        )
    if birth_setting:
        items.append(
            _birth_journey_plan_item(
                "问清生产医院入院入口和证件",
                f"问清{birth_setting}的预登记、夜间入口、陪产探视和证件要求。",
                "未来 7 天",
                ["birth_setting"],
                steps=["确认预登记或建档入口", "记录夜间急诊或入院入口", "列出证件和陪产探视要求"],
                done_criteria="已记录入院入口、预登记、证件材料和陪产探视规则。",
                after_done_value="做完后，临产时不用临时查入口、材料和陪同规则。",
            )
        )
    if support:
        items.append(
            _birth_journey_plan_item(
                "和支持人确认 4 项临产分工",
                f"明确{support}负责联系医院、拿材料、出发和记录医嘱。",
                "未来 7 天",
                ["support_person"],
                steps=["定谁负责联系医院", "定谁拿证件和住院材料", "定谁记录医生口径和出发安排"],
                done_criteria="已和支持人确认联系医院、拿材料、出发、记录医嘱这 4 项分工。",
                after_done_value="做完后，临产时每个人知道自己负责什么，减少现场混乱。",
            )
        )
    if feeding:
        items.append(
            _birth_journey_plan_item(
                "列出产后 48 小时喂养求助问题",
                "写清亲喂/混合/泵奶选择，以及何时找护士或 IBCLC。",
                "未来 7 天",
                ["feeding_intention", "feeding_ibclc_context"],
                steps=["写下喂养偏好和担心点", "问医院能否找护士或 IBCLC", "列出产后 48 小时要问的问题"],
                done_criteria="已列出住院前要确认的喂养支持问题。",
                after_done_value="做完后，产后最初 48 小时遇到含乳、涨奶或泵奶问题时更知道找谁。",
            )
        )
    if "剖" in birth_path:
        items.append(
            _birth_journey_plan_item(
                "补问剖宫产术前术后 4 件事",
                "确认禁食禁水、入院时间、下床和伤口观察。",
                "下次产检",
                ["birth_path"],
                steps=["问术前禁食禁水时间", "问入院和手术前流程", "问术后下床和伤口观察"],
                done_criteria="已记录禁食禁水、入院时间、术后下床和伤口观察口径。",
                after_done_value="做完后，剖宫产前后最容易慌的流程会提前变清楚。",
            )
        )
    if lifestyle_text:
        items.append(_birth_journey_lifestyle_next_7_item(lifestyle_text))
    return _unique_birth_journey_plan_items(items)


def _birth_journey_next_2_4_week_items(week: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    birth_path = _birth_journey_substantive_text(context.get("birth_path"))
    birth_setting = _birth_journey_substantive_text(context.get("birth_setting"))
    feeding = _birth_journey_substantive_text(context.get("feeding_intention")) or _birth_journey_substantive_text(context.get("feeding_ibclc_context"))
    fetus_count = _birth_journey_substantive_text(context.get("fetus_count"))
    if not isinstance(week, int):
        return [
            _birth_journey_plan_item("补充孕周后换算检查窗口", "补齐后才能定位 NT、大排畸、糖耐等窗口。", "补充信息后", ["current_week"]),
            _birth_journey_plan_item("整理产检节奏和医院要求", "先确认下次产检、医院材料和医生备注。", "未来 2-4 周", ["checkup_status"]),
        ]
    items: list[dict[str, Any]] = []
    if week < 14:
        items.append(_birth_journey_plan_item("完成建档和早孕筛查闭环", "确认建档材料、筛查时间、报告领取和复查路径。", f"孕 {week + 1}-{min(14, week + 4)} 周", ["current_week"]))
    elif week < 24:
        items.append(_birth_journey_plan_item("跟进大排畸和常规产检", "重点看结构筛查、胎儿生长和医生要求的复查。", f"孕 {week + 1}-{week + 4} 周", ["current_week"]))
    elif week < 28:
        items.append(_birth_journey_plan_item("完成糖耐并确认复查重点", "一起回看糖耐、血常规、尿常规、血压和胎儿生长。", f"孕 {week + 1}-{min(28, week + 4)} 周", ["current_week"]))
    elif week < 32:
        items.append(_birth_journey_plan_item("关注贫血、生长、胎位和胎动", "把血压水肿、胎动、胎位和胎儿生长放到固定观察里。", f"孕 {week + 1}-{week + 4} 周", ["current_week"]))
    else:
        items.append(_birth_journey_plan_item("确认入院流程和待产准备", "落实待产包、证件、入院信号、路线和陪产探视。", f"孕 {week + 1}-{week + 4} 周", ["current_week"]))
    if birth_path:
        items.append(_birth_journey_plan_item("确认分娩方式相关问题", "问清适用条件、风险提示、变更口径和住院流程差异。", "下次产检", ["birth_path"]))
    if birth_setting:
        items.append(_birth_journey_plan_item("按医院规则更新准备清单", f"同步{birth_setting}的入院材料、陪产探视和待产包限制。", "未来 2-4 周", ["birth_setting"]))
    if feeding:
        items.append(_birth_journey_plan_item("规划住院后的喂养支持", "提前列出含乳、涨奶、泵奶和 IBCLC 求助方式。", "未来 2-4 周", ["feeding_intention", "feeding_ibclc_context"]))
    if any(token in fetus_count for token in ("双", "多", "三")):
        items.append(_birth_journey_plan_item("按多胎确认监测和入院节奏", "问清胎儿生长、早产风险、复查频率和入院时机。", "未来 2-4 周", ["fetus_count"]))
    return items


def _birth_journey_later_milestones(week: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    milestones = [
        (14, _birth_journey_plan_item("12-14 周：完成 NT/早筛和建档", "确认报告、复查口径、下次产检和建档材料。", "12-14 周", ["milestone"])),
        (24, _birth_journey_plan_item("18-24 周：完成大排畸", "重点看结构筛查、胎盘羊水和医生要求的复查。", "18-24 周", ["milestone"])),
        (28, _birth_journey_plan_item("24-28 周：完成糖耐", "一起回看糖耐、血常规、尿常规、血压和胎儿生长。", "24-28 周", ["milestone"])),
        (32, _birth_journey_plan_item("28-32 周：建立观察节奏", "关注胎动、血压水肿、贫血和胎儿生长。", "28-32 周", ["milestone"])),
        (36, _birth_journey_plan_item("32-36 周：落实待产准备", "整理证件、待产包、分娩偏好和喂养求助方式。", "32-36 周", ["milestone"])),
        (42, _birth_journey_plan_item("36 周后：确认入院安排", "确认入院信号、路线、材料和陪产分工。", "36 周后", ["milestone"])),
        (99, _birth_journey_plan_item("产后 0-42 天：保留恢复支持", "出院前确认复诊、身体恢复和喂养求助方式。", "产后 0-42 天", ["milestone"])),
    ]
    if not isinstance(week, int):
        return [item for _, item in milestones[3:]]
    return [item for end_week, item in milestones if end_week >= week]


def _birth_journey_base_phase(phase_id: str) -> dict[str, Any]:
    base: dict[str, dict[str, Any]] = {
            "early_pregnancy": {
                "goal": "确认怀孕情况，顺利完成首次产检，把重要信息准备好。",
                "watchouts": ["现阶段先关注产检和身体变化，不用着急考虑生产和待产准备。"],
                "actions": ["确认首次产检或建档时间", "整理检查结果、用药和补充剂信息", "记下想咨询医生的问题"],
                "comate_help": [],
            },
            "mid_pregnancy": {
                "goal": "关注宝宝发育，跟上产检节奏，并开始规划生产和产后支持。",
                "watchouts": ["很多事情不用一次准备完成，先把医院选择和家庭支持安排理顺。"],
                "actions": ["准备下次产检想问的问题", "了解生产医院和相关流程", "和家人讨论产后支持安排"],
                "comate_help": [],
            },
            "late_pregnancy": {
                "goal": "逐步落实生产前准备，让临产时更从容。",
                "watchouts": ["距离生产越来越近，提前做好准备会让临产和住院过程更顺利。"],
                "actions": ["确认医院入院和陪产要求", "准备待产包和重要证件", "和家人明确临产时的分工安排", "提前想好分娩和喂养方面的重要偏好"],
                "comate_help": ["制定个性化待产清单"],
            },
            "labor_recognition": {
                "goal": "了解临产信号，知道什么时候联系医院、什么时候出发。",
                "watchouts": ["如果出现破水、大量出血、胎动明显减少或其他异常情况，请及时联系医院。"],
                "actions": ["保存医院和重要联系人的电话", "熟悉去医院的路线和交通方案", "把证件和住院材料放在容易拿取的位置", "留意宫缩和身体变化"],
                "comate_help": [],
            },
            "hospital_birth": {
                "goal": "专注分娩和恢复，把重要沟通和记录安排好。",
                "watchouts": ["医疗决策以医护团队建议为准，有任何需求或担忧都可以及时沟通。"],
                "actions": ["和医护确认你的重点需求", "记录妈妈和宝宝的重要情况", "出院前确认复诊和护理事项"],
                "comate_help": [],
            },
            "postpartum": {
                "goal": "关注妈妈恢复和宝宝喂养，让家庭逐步适应新的节奏。",
                "watchouts": ["如果妈妈或宝宝出现异常情况，请及时联系医生、儿科医生或 IBCLC。"],
                "actions": ["记录喂养、尿布和宝宝情况", "关注身体恢复情况", "安排夜间照护和休息时间", "遇到喂养问题及时寻求支持"],
                "comate_help": [],
            },
    }
    return dict(base.get(phase_id) or base["late_pregnancy"])


def _personalize_birth_journey_phase(phase: dict[str, Any], context: dict[str, Any]) -> None:
    phase_id = str(phase.get("id") or "")
    first_birth = str(context.get("first_birth") or "")
    fetus_count = str(context.get("fetus_count") or "")
    birth_path = str(context.get("birth_path") or "")
    feeding = str(context.get("feeding_intention") or "")
    birth_setting = str(context.get("birth_setting") or "")
    support = str(context.get("support_person") or "")
    medical_notes = context.get("medical_notes") if isinstance(context.get("medical_notes"), list) else []

    if first_birth == "是" and phase_id in {"late_pregnancy", "labor_recognition"}:
        phase["actions"].append("你是第一胎，可以提前让支持人也看一遍入院流程和临产信号，避免到时只靠你一个人判断。")
    if first_birth == "否" and phase_id == "late_pregnancy":
        phase["actions"].append("提前安排大宝接送、陪伴和夜间照护，临产时不要临时找人。")
    if any(token in fetus_count for token in ("双", "多", "三")) and phase_id == "late_pregnancy":
        phase["watchouts"].append("你是多胎，产检和入院节奏更要按医生给出的安排来，别用单胎时间表硬套。")
    if "剖" in birth_path and phase_id in {"late_pregnancy", "hospital_birth", "postpartum"}:
        phase["actions"].append("你是剖宫产，提前问清术前禁食、入院时间、住院天数和术后下床/伤口护理口径。")
    if any(token in feeding for token in ("母乳", "混合", "纯泵")) and phase_id in {"hospital_birth", "postpartum"}:
        phase["actions"].append("你希望母乳或混合喂养，入院后可以尽早确认含乳、涨奶处理和 IBCLC/泌乳顾问支持。")
    if birth_setting and phase_id == "late_pregnancy":
        phase["actions"].append(f"围绕{birth_setting}确认预登记、陪产、探视、夜间入口和停车/打车规则。")
    if support and phase_id in {"late_pregnancy", "postpartum"}:
        phase["actions"].append(f"把{support}要负责的事提前写下来：出发、联系医院、记录、夜间照护和补给。")
    if medical_notes and phase_id in {"mid_pregnancy", "late_pregnancy"}:
        phase["watchouts"].append("医生已经提醒过的特殊情况要以医院方案为准，产检时把后续观察和入院时机问清楚。")


def _mark_birth_journey_current_phase(phases: list[dict[str, Any]]) -> None:
    if not phases:
        return
    current_seen = False
    for phase in phases:
        if phase.get("status") == "current" and not current_seen:
            current_seen = True
        elif phase.get("status") == "current":
            phase["status"] = "upcoming"
    if not current_seen:
        phases[0]["status"] = "current"


def _birth_journey_subtitle(timeline: dict[str, Any], scope: str) -> str:
    week = timeline.get("current_week")
    if scope == "prenatal_only":
        return f"从孕{week}周到生产前的阶段路线图" if week else "从现在到生产前的阶段路线图"
    if scope == "short_range":
        return f"从孕{week}周开始的近期准备节奏" if week else "从现在开始的近期准备节奏"
    return f"从孕{week}周到产后 42 天的阶段路线图" if week else "从现在到产后 42 天的阶段路线图"


def _birth_journey_next_action(timeline: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
    week = timeline.get("current_week")
    if isinstance(week, int) and week < 28:
        return {"label": "整理产检问题", "send_text": "帮我整理下次产检要问的 3-5 个问题"}
    if isinstance(week, int) and week < 32:
        return {"label": "确认医院流程", "send_text": "帮我整理需要向医院确认的生产流程问题"}
    if isinstance(week, int) and week >= 35:
        return {"label": "继续整理待产包", "send_text": "帮我整理一份随时能出发的待产包清单"}
    if isinstance(week, int):
        return {"label": "整理待产包", "send_text": "帮我整理一份个性化待产包清单"}
    if str(context.get("birth_path") or "") or str(context.get("support_person") or ""):
        return {"label": "整理分娩沟通单", "send_text": "帮我把生产偏好整理成分娩沟通单"}
    return {"label": "补充孕周", "send_text": "我现在大概孕几周"}


def _birth_journey_gestational_days(due_text: str, today: date, inputs: RuntimeInputs) -> int | None:
    due_date = _date_from_text(due_text)
    if due_date is not None:
        days = 280 - (due_date - today).days
        return max(1, min(294, days)) if days >= 0 else None
    text = str(due_text or "")
    match = re.search(r"(\d{1,2})\s*(?:周|w|week)?\s*(?:\+|加)?\s*(\d{1,2})?", text, re.IGNORECASE)
    if match:
        weeks = int(match.group(1))
        if 1 <= weeks <= 42:
            days = int(match.group(2) or 0)
            return max(1, min(294, weeks * 7 + days))
    week = _pregnancy_week(due_text, inputs)
    return week * 7 if week is not None else None


def _birth_journey_due_date(due_text: str, today: date, gestational_days: int | None) -> date | None:
    due_date = _date_from_text(due_text)
    if due_date is not None:
        return due_date
    if gestational_days is None:
        return None
    return today + timedelta(days=max(0, 280 - gestational_days))


def _date_from_text(value: str) -> date | None:
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(value or ""))
    if not match:
        return None
    return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _birth_journey_date_range(start_date: date, end_date: date, estimated: bool) -> str:
    prefix = "约 " if estimated else ""
    return f"{prefix}{_format_birth_journey_date(start_date)}～{_format_birth_journey_date(end_date)}"


def _format_birth_journey_date(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%Y/%m/%d")
    return ""


def _unique_text_list(value: Any, max_items: int) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for item in _text_list(value):
        if item in seen:
            continue
        seen.add(item)
        items.append(item)
        if len(items) >= max_items:
            break
    return items


def _unique_birth_journey_items(value: Any, max_items: int) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    seen: set[str] = set()
    items: list[str] = []
    for raw_item in raw_items:
        text = _first_text(raw_item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


def _build_hospital_bag_card_json(form_data: dict[str, Any], generation_mode: str, inputs: RuntimeInputs) -> dict[str, Any]:
    due_date_or_week = _first_text(form_data.get("due_date_or_week")) or "待确认"
    birth_path = _normalize_hospital_bag_birth_path(_first_nonempty_text(form_data.get("birth_path"))) or "待确认"
    feeding_intention = _normalize_feeding_intention(_first_nonempty_text(form_data.get("feeding_intention"))) or "待确认"
    first_birth = _normalize_first_birth(_first_text(form_data.get("first_birth"))) or "待确认"
    fetus_count = _normalize_hospital_bag_fetus_count(_first_nonempty_text(form_data.get("fetus_count"))) or "待确认"
    pregnancy_history_or_notes = _text_list(form_data.get("pregnancy_history_or_notes"))
    return_to_work_timing = _first_text(form_data.get("return_to_work_timing")) or "待确认"
    top_worries = _text_list(form_data.get("top_worries"))
    birth_setting = _first_text(form_data.get("birth_setting")) or "待确认"
    expected_stay = _first_text(form_data.get("expected_stay")) or "待确认"
    support_person = _normalize_hospital_bag_support_person(_first_nonempty_text(form_data.get("support_person"))) or "待确认"
    provided_items = _split_hospital_provided_items(form_data.get("hospital_provided_items"))
    stage = _hospital_bag_stage(due_date_or_week, generation_mode, inputs)
    missing_fields = [
        label
        for field_id, label in HOSPITAL_BAG_MISSING_LABELS.items()
        if not _has_confirmed_hospital_bag_form_value(field_id, form_data.get(field_id))
    ]
    context: dict[str, Any] = {
        "stage": stage,
        "birth_path": birth_path,
        "feeding_intention": feeding_intention,
        "first_birth": first_birth,
        "fetus_count": fetus_count,
        "pregnancy_history_or_notes": pregnancy_history_or_notes,
        "return_to_work_timing": return_to_work_timing,
        "top_worries": top_worries,
        "expected_stay": expected_stay,
        "support_person": support_person,
        "provided_items": provided_items,
    }
    hospital_questions = _hospital_bag_confirmation_questions(context)
    packing_groups = _hospital_bag_packing_groups(context)
    personalized_notes = _hospital_bag_personalized_notes(context, form_data)
    timeline = _hospital_bag_timeline(stage)
    focus_items = _hospital_bag_focus_items(stage, context)
    return {
        "card_type": "hospital_bag_card",
        "schema_version": "1.0",
        "title": "待产包",
        "subtitle": "个性化入院物品清单",
        "owner": {
            "due_date_or_week": due_date_or_week,
            "birth_setting": birth_setting,
            "birth_path": birth_path,
            "first_birth": first_birth,
            "feeding_intention": feeding_intention,
            "support_person": support_person,
            "fetus_count": fetus_count,
            "return_to_work_timing": return_to_work_timing,
        },
        "hospital_context": {
            "expected_stay": expected_stay,
            "hospital_provided_items": provided_items,
            "items_to_confirm_with_hospital": hospital_questions[:5],
        },
        "focus_items": focus_items,
        "hospital_questions": hospital_questions[:8],
        "packing_groups": packing_groups,
        "missing_or_to_buy": [],
        "timeline": timeline,
        "personalized_notes": personalized_notes,
        "missing_fields": missing_fields,
        "disclaimer": HOSPITAL_BAG_DISCLAIMER,
    }


def _hospital_bag_packing_groups(context: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = [
        {"group_id": "documents", "title": "证件文件包", "items": _hospital_bag_document_items(context)},
        {"group_id": "mom_hospital_bag", "title": "妈妈住院包", "items": _hospital_bag_mom_items(context)},
        {"group_id": "baby_discharge_bag", "title": "宝宝出院包", "items": _hospital_bag_baby_items(context)},
        {"group_id": "support_person_bag", "title": "陪产人包", "items": _hospital_bag_support_items(context)},
        {"group_id": "car_backup_bag", "title": "车上备用包", "items": _hospital_bag_car_items(context)},
    ]
    postpartum_items = _hospital_bag_postpartum_items(context)
    if postpartum_items:
        groups.append({"group_id": "postpartum_home_first_week", "title": "产后回家第一周用品", "items": postpartum_items})
    filtered_groups = [
        {**group, "items": _filter_provided_items(group["items"], context.get("provided_items", []))}
        for group in groups
        if group.get("items")
    ]
    _suppress_hospital_bag_personalization(filtered_groups)
    _apply_hospital_bag_item_explanations(filtered_groups)
    return filtered_groups


def _suppress_hospital_bag_personalization(groups: list[Any]) -> None:
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = _first_text(group.get("group_id"))
        suppress_group = group_id in HOSPITAL_BAG_REASON_SUPPRESSED_GROUP_IDS
        items = group.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            label = _first_text(item.get("label"))
            if suppress_group or label in HOSPITAL_BAG_REASON_SUPPRESSED_ITEM_LABELS:
                item.pop("personalized_by", None)


def _personalization_source(field_id: str, condition: Any, effect: str = "调整") -> dict[str, str] | None:
    condition_text = _first_text(condition)
    if _normalized_placeholder(condition_text) in PLACEHOLDER_VALUES:
        return None
    return {
        "field": field_id,
        "field_label": HOSPITAL_BAG_FIELD_LABELS.get(field_id, field_id),
        "condition": condition_text,
        "effect": effect,
    }


def _with_personalized_by(item: dict[str, Any], *sources: dict[str, str] | None) -> dict[str, Any]:
    cleaned = [source for source in sources if source]
    if not cleaned:
        return item
    existing = item.get("personalized_by")
    if isinstance(existing, list):
        cleaned = [*existing, *cleaned]
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for source in cleaned:
        key = (
            str(source.get("field") or ""),
            str(source.get("condition") or ""),
            str(source.get("effect") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    next_item = dict(item)
    next_item["personalized_by"] = deduped
    return next_item


def _stage_source(context: dict[str, Any], effect: str = "调整") -> dict[str, str] | None:
    stage = str(context.get("stage") or "")
    stage_labels = {
        "planning": "32周前计划版",
        "purchase": "32-35周采购版",
        "packing": "36周打包版",
        "immediate": "37周后临产版",
    }
    return _personalization_source("due_date_or_week", stage_labels.get(stage, stage), effect)


def _context_source(context: dict[str, Any], field_id: str, effect: str = "调整") -> dict[str, str] | None:
    return _personalization_source(field_id, context.get(field_id), effect)


def _multi_source(field_id: str, condition: str, effect: str = "调整") -> dict[str, str] | None:
    return _personalization_source(field_id, condition, effect)


def _has_context_choice(context: dict[str, Any], field_id: str, *tokens: str) -> bool:
    values = _text_list(context.get(field_id))
    return any(any(token in value for token in tokens) for value in values)


def _hospital_bag_document_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    pregnancy_source = None
    if _meaningful_pregnancy_history(context):
        pregnancy_source = _multi_source("pregnancy_history_or_notes", "已填写医生提示", "提权")
    first_birth_source = _context_source(context, "first_birth", "新增") if context.get("first_birth") == "是" else None
    prior_birth_source = _context_source(context, "first_birth", "新增") if context.get("first_birth") == "否" else None
    timing_worry_source = (
        _multi_source("top_worries", "不知道什么时候去医院", "提权")
        if _has_context_choice(context, "top_worries", "不知道什么时候去医院")
        else None
    )
    support_source = (
        _context_source(context, "support_person", "提权")
        if _support_is_limited(context)
        else None
    )
    items = [
        {"label": "身份证件", "priority": "must", "copy_requirement": "原件"},
        {"label": "医保卡/保险卡", "priority": "must", "copy_requirement": "原件"},
        {"label": "产检本/产检资料", "priority": "must", "copy_requirement": "原件"},
        _with_personalized_by(
            {"label": "检查报告/化验单", "priority": "must" if pregnancy_source else "recommended", "copy_requirement": "按医院要求"},
            pregnancy_source,
        ),
        _with_personalized_by(
            {"label": "医院预登记信息", "priority": "confirm_first", "confirm_question": "确认是否已完成医院预登记，以及入院当天需要出示什么。"},
            first_birth_source,
        ),
        {"label": "银行卡/手机支付", "priority": "must"},
        _with_personalized_by({"label": "紧急联系人信息", "priority": "recommended"}, support_source, timing_worry_source),
        _with_personalized_by({"label": "医生/医院联系电话", "priority": "recommended"}, pregnancy_source, timing_worry_source),
        _with_personalized_by(
            {
                "label": "分娩沟通单",
                "priority": "recommended" if first_birth_source else "nice_to_have",
                "explain": "记录生产偏好和需要提前沟通的事，入院时方便给医护看。",
            },
            first_birth_source,
        ),
        {
            "label": "准生证/户口本",
            "priority": "confirm_first",
            "confirm_question": "确认医院是否要求携带准生证、户口本及复印件。",
        },
    ]
    if prior_birth_source:
        items.append(
            _with_personalized_by(
                {"label": "大宝照护安排", "priority": "recommended", "note": "入院、住院和出院当天分别确认谁负责。"},
                prior_birth_source,
            )
        )
    return items


def _hospital_bag_mom_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    stage = str(context.get("stage") or "")
    stage_priority_source = _stage_source(context, "提权") if stage in {"packing", "immediate"} else _stage_source(context, "暂缓囤货")
    birth_path_source = _context_source(context, "birth_path", "新增") if context.get("birth_path") == "剖宫产" else None
    birth_path_priority_source = _context_source(context, "birth_path", "提权") if context.get("birth_path") == "剖宫产" else None
    c_section_worry_source = (
        _multi_source("top_worries", "怕剖宫产恢复", "新增")
        if _has_context_choice(context, "top_worries", "怕剖宫产恢复")
        else None
    )
    feeding_source = (
        _context_source(context, "feeding_intention", "保留")
        if context.get("feeding_intention") in {"母乳", "混合", "纯泵奶", "未确定", "待确认"}
        else None
    )
    support_source = (
        _context_source(context, "support_person", "提权")
        if _support_is_limited(context)
        else None
    )
    postpartum_priority = "must" if stage in {"packing", "immediate"} else "recommended"
    disposable_underwear_priority = "must" if stage in {"packing", "immediate"} else "recommended"
    items = [
        _with_personalized_by(
            {"label": "手机充电线和充电器", "priority": "must", "note": "长充电线更适合病床旁使用。"},
            support_source,
            birth_path_priority_source,
        ),
        {"label": "宽松出院衣物", "priority": "must", "quantity": "1套"},
        _with_personalized_by(
            {"label": "开襟睡衣/哺乳睡衣", "priority": "recommended", "quantity": "1-2套"},
            birth_path_priority_source,
            feeding_source,
        ),
        _with_personalized_by(
            {"label": "哺乳文胸/舒适内衣", "priority": "recommended", "quantity": "2-3件"},
            feeding_source,
        ),
        {"label": "防滑拖鞋", "priority": "must", "quantity": "1双"},
        {"label": "吸管杯", "priority": "must", "quantity": "1个"},
        _with_personalized_by(
            {"label": "产褥垫/产妇卫生巾", "priority": postpartum_priority, "quantity": _postpartum_pad_quantity(context)},
            stage_priority_source,
            _context_source(context, "birth_path", "数量调整") if context.get("birth_path") == "剖宫产" else None,
        ),
        _with_personalized_by(
            {"label": "一次性内裤", "priority": disposable_underwear_priority, "quantity": "若干条"},
            stage_priority_source,
        ),
        {"label": "洗漱用品", "priority": "recommended", "quantity": "旅行装"},
        {"label": "纸巾/湿巾", "priority": "recommended", "quantity": "少量"},
        {"label": "毛巾", "priority": "recommended", "quantity": "1-2条"},
        {"label": "束发用品", "priority": "nice_to_have"},
        {"label": "外套/披肩", "priority": "recommended", "quantity": "1件"},
        {
            "label": "胎监带",
            "priority": "confirm_first",
            "confirm_question": "确认医院是否要求自带胎监带，以及需要几条。",
        },
    ]
    if context.get("birth_path") == "剖宫产" or c_section_worry_source:
        source = birth_path_source or c_section_worry_source
        items.insert(2, _with_personalized_by({"label": "高腰宽松内裤", "priority": "recommended", "quantity": "若干条", "note": "更不容易压到腹部。"}, source))
        items.insert(3, _with_personalized_by({"label": "不压腹出院裤/裙", "priority": "recommended", "quantity": "1套"}, source))
        items.append(
            _with_personalized_by(
                {
                    "label": "收腹带",
                    "priority": "confirm_first",
                    "confirm_question": "剖宫产先确认医生或医院是否建议使用收腹带。",
                },
                source,
            )
        )
    if _has_context_choice(context, "pregnancy_history_or_notes", "妊娠糖尿病"):
        items.append(
            _with_personalized_by(
                {"label": "血糖记录/饮食医嘱", "priority": "must", "note": "只按医生已经给出的方案准备。"},
                _multi_source("pregnancy_history_or_notes", "妊娠糖尿病", "新增"),
            )
        )
        items.append(
            _with_personalized_by(
                {"label": "医生允许的加餐", "priority": "confirm_first", "confirm_question": "确认产房和病区允许携带的食物类型。"},
                _multi_source("pregnancy_history_or_notes", "妊娠糖尿病", "新增"),
            )
        )
    if _has_context_choice(context, "pregnancy_history_or_notes", "血压", "子痫"):
        items.append(
            _with_personalized_by(
                {"label": "血压记录/用药清单", "priority": "must", "note": "只记录医生已确认的信息，不自行调整用药。"},
                _multi_source("pregnancy_history_or_notes", "血压或子痫前期风险", "新增"),
            )
        )
    if _has_context_choice(context, "pregnancy_history_or_notes", "胎盘问题"):
        items.append(
            _with_personalized_by(
                {"label": "近期B超/医生医嘱", "priority": "must", "copy_requirement": "按医院要求"},
                _multi_source("pregnancy_history_or_notes", "胎盘问题", "提权"),
            )
        )
    if support_source:
        items.append(
            _with_personalized_by(
                {"label": "床边收纳袋", "priority": "recommended", "note": "把手机、证件、水杯和护理用品放在伸手可及的位置。"},
                support_source,
            )
        )
    return items


def _hospital_bag_baby_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    fetus_count = str(context.get("fetus_count") or "")
    fetus_source = _context_source(context, "fetus_count", "数量调整") if fetus_count in {"双胎", "三胎及以上"} else None
    baby_worry_source = (
        _multi_source("top_worries", "怕宝宝用品准备不全", "提权")
        if _has_context_choice(context, "top_worries", "怕宝宝用品准备不全")
        else None
    )
    nicu_source = (
        _multi_source("pregnancy_history_or_notes", "宝宝可能 NICU", "新增")
        if _has_context_choice(context, "pregnancy_history_or_notes", "NICU")
        else None
    )
    early_birth_source = (
        _multi_source("pregnancy_history_or_notes", "早产风险", "新增")
        if _has_context_choice(context, "pregnancy_history_or_notes", "早产")
        else None
    )
    baby_quantity = _baby_quantity(context, "1套")
    blanket_quantity = _baby_quantity(context, "1条")
    small_item_quantity = _baby_quantity(context, "各1-2件")
    items = [
        _with_personalized_by({"label": "宝宝出院衣物", "priority": "must", "quantity": baby_quantity}, fetus_source, baby_worry_source),
        _with_personalized_by({"label": "备用连体衣", "priority": "recommended", "quantity": _baby_quantity(context, "1-2套")}, fetus_source, baby_worry_source),
        _with_personalized_by({"label": "包被", "priority": "must", "quantity": blanket_quantity}, fetus_source, baby_worry_source),
        _with_personalized_by({"label": "小毯子", "priority": "nice_to_have", "quantity": blanket_quantity}, fetus_source),
        _with_personalized_by({"label": "纸尿裤", "priority": "confirm_first", "confirm_question": "确认医院是否提供纸尿裤；如果不提供，再问建议数量。"}, fetus_source, baby_worry_source),
        _with_personalized_by({"label": "湿巾/棉柔巾", "priority": "recommended", "quantity": _baby_quantity(context, "1-2包")}, fetus_source, baby_worry_source),
        _with_personalized_by({"label": "帽子/袜子", "priority": "recommended", "quantity": small_item_quantity}, fetus_source, baby_worry_source),
        {"label": "口水巾/小方巾", "priority": "nice_to_have", "quantity": "2-3条"},
        {"label": "奶瓶", "priority": "confirm_first", "confirm_question": "确认医院是否允许或需要自带奶瓶。"},
        _with_personalized_by(
            {"label": "安全提篮/安全座椅", "priority": "confirm_first", "confirm_question": "确认出院交通是否需要安全提篮或安全座椅。"},
            fetus_source,
        ),
    ]
    if nicu_source or early_birth_source:
        source = nicu_source or early_birth_source
        items.append(
            _with_personalized_by(
                {"label": "NICU探视/送奶规则确认", "priority": "confirm_first", "confirm_question": "确认宝宝如需 NICU 时，探视、送奶和标签要求。"},
                source,
            )
        )
    if early_birth_source:
        items.append(
            _with_personalized_by(
                {"label": "小码/早产儿衣物确认", "priority": "confirm_first", "confirm_question": "先问医院是否需要自备特殊尺码衣物。"},
                early_birth_source,
            )
        )
    return items


def _hospital_bag_support_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    support_source = _context_source(context, "support_person", "调整")
    prior_birth_source = _context_source(context, "first_birth", "新增") if context.get("first_birth") == "否" else None
    if not _support_person_needs_bag(context):
        return [
            _with_personalized_by({"label": "远程联系人名单", "priority": "must", "note": "写清楚临产、入院和出院时分别联系谁。"}, support_source),
            _with_personalized_by({"label": "去医院交通方案", "priority": "must"}, support_source),
            _with_personalized_by({"label": "出院接送安排", "priority": "recommended"}, support_source),
            _with_personalized_by({"label": "家中照护安排", "priority": "recommended", "note": "如有大宝、宠物或家务支持，提前定好负责人。"}, support_source, prior_birth_source),
            _with_personalized_by({"label": "紧急备用联系人", "priority": "recommended"}, support_source),
        ]
    return [
        _with_personalized_by({"label": "陪产人身份证件", "priority": "must", "copy_requirement": "原件"}, support_source),
        _with_personalized_by({"label": "手机充电器", "priority": "must"}, support_source),
        _with_personalized_by({"label": "充电宝", "priority": "recommended"}, support_source),
        _with_personalized_by({"label": "换洗衣物", "priority": "recommended", "quantity": "1套"}, support_source),
        _with_personalized_by({"label": "外套", "priority": "recommended", "quantity": "1件"}, support_source),
        _with_personalized_by({"label": "洗漱用品", "priority": "recommended", "quantity": "1套"}, support_source),
        _with_personalized_by({"label": "水和零食", "priority": "recommended", "quantity": "按住院天数"}, support_source),
        _with_personalized_by({"label": "停车/支付用品", "priority": "recommended"}, support_source),
        _with_personalized_by({"label": "记录工具", "priority": "nice_to_have", "note": "用于记医生交代、出生信息和喂养时间。"}, support_source),
        _with_personalized_by({"label": "妈妈的沟通偏好", "priority": "nice_to_have", "note": "提前知道哪些事要先问妈妈。"}, support_source),
    ]


def _hospital_bag_car_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    support_source = _context_source(context, "support_person", "提权") if _support_is_limited(context) else None
    timing_worry_source = (
        _multi_source("top_worries", "不知道什么时候去医院", "提权")
        if _has_context_choice(context, "top_worries", "不知道什么时候去医院")
        else None
    )
    first_birth_source = _context_source(context, "first_birth", "提权") if context.get("first_birth") == "是" else None
    return [
        {"label": "备用产褥垫/卫生巾", "priority": "recommended", "quantity": "少量"},
        {"label": "纸巾/湿巾", "priority": "recommended", "quantity": "少量"},
        {"label": "水", "priority": "recommended", "quantity": "少量"},
        {"label": "备用衣物", "priority": "nice_to_have", "quantity": "1套"},
        _with_personalized_by({"label": "医院路线和停车信息", "priority": "recommended"}, support_source, timing_worry_source),
        {"label": "塑料袋/收纳袋", "priority": "recommended"},
        {"label": "备用毛巾", "priority": "nice_to_have", "quantity": "1条"},
        {"label": "车内充电线", "priority": "recommended"},
        _with_personalized_by(
            {"label": "夜间入口信息", "priority": "confirm_first", "confirm_question": "确认夜间急诊或产科入口在哪里。"},
            first_birth_source,
            timing_worry_source,
        ),
    ]


def _hospital_bag_postpartum_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    feeding = context.get("feeding_intention")
    feeding_source = _context_source(context, "feeding_intention", "新增")
    return_work_source = (
        _context_source(context, "return_to_work_timing", "提权")
        if _early_return_to_work(context)
        else None
    )
    milk_worry_source = (
        _multi_source("top_worries", "怕母乳不够", "提权")
        if _has_context_choice(context, "top_worries", "怕母乳不够")
        else None
    )
    nicu_source = (
        _multi_source("pregnancy_history_or_notes", "宝宝可能 NICU", "提权")
        if _has_context_choice(context, "pregnancy_history_or_notes", "NICU")
        else None
    )
    if feeding == "配方":
        return [
            _with_personalized_by({"label": "奶瓶", "priority": "confirm_first", "confirm_question": "确认医院是否允许自带奶瓶，或是否由医院提供。"}, feeding_source),
            _with_personalized_by({"label": "配方奶", "priority": "confirm_first", "confirm_question": "确认医院是否允许自带配方奶，以及品牌或规格要求。"}, feeding_source),
            _with_personalized_by({"label": "奶瓶清洁用品", "priority": "recommended", "quantity": "少量"}, feeding_source),
            _with_personalized_by({"label": "奶嘴", "priority": "recommended", "quantity": "少量"}, feeding_source),
            _with_personalized_by({"label": "消毒设备", "priority": "nice_to_have", "note": "按家庭习惯准备，不一定需要提前买大件。"}, feeding_source),
            _with_personalized_by({"label": "喂养记录工具", "priority": "recommended"}, feeding_source),
        ]
    items = [
        _with_personalized_by({"label": "哺乳文胸/哺乳背心", "priority": "recommended", "quantity": "2-3件"}, feeding_source),
        _with_personalized_by({"label": "防溢乳垫", "priority": "recommended", "quantity": "5-10片"}, feeding_source),
        _with_personalized_by(
            {
                "label": "便携式吸奶器",
                "priority": "recommended" if not (return_work_source or nicu_source or milk_worry_source) else "must",
                "quantity": "1台",
                "note": "母乳或混合喂养时可作为备用，不是必须购买。",
            },
            feeding_source,
            return_work_source,
            nicu_source,
            milk_worry_source,
        ),
        _with_personalized_by(
            {"label": "储奶袋/储奶瓶", "priority": "recommended" if not (return_work_source or nicu_source) else "must", "quantity": "少量"},
            feeding_source,
            return_work_source,
            nicu_source,
        ),
        _with_personalized_by({"label": "乳头霜", "priority": "recommended", "quantity": "1支"}, feeding_source, milk_worry_source),
        _with_personalized_by({"label": "乳盾", "priority": "confirm_first", "confirm_question": "是否需要乳盾，建议先听医院或哺乳顾问建议。"}, feeding_source, milk_worry_source),
        _with_personalized_by({"label": "哺乳枕", "priority": "nice_to_have"}, feeding_source),
        {"label": "小夜灯", "priority": "nice_to_have"},
        {"label": "宝宝尿布台用品", "priority": "recommended"},
        _with_personalized_by({"label": "喂养记录工具", "priority": "recommended"}, feeding_source),
    ]
    if return_work_source:
        items.extend(
            [
                _with_personalized_by({"label": "冷藏包/冰袋", "priority": "recommended", "quantity": "1套"}, return_work_source),
                _with_personalized_by({"label": "标签笔", "priority": "recommended", "quantity": "1支"}, return_work_source, nicu_source),
                _with_personalized_by({"label": "吸奶配件清洁包", "priority": "recommended", "quantity": "1套"}, return_work_source),
            ]
        )
    if feeding == "混合":
        items.extend(
            [
                _with_personalized_by({"label": "奶瓶", "priority": "confirm_first", "confirm_question": "确认医院是否允许或需要自带奶瓶。"}, feeding_source),
                _with_personalized_by({"label": "奶瓶清洁用品", "priority": "recommended", "quantity": "少量"}, feeding_source),
            ]
        )
    return items if feeding in {"母乳", "混合", "纯泵奶", "待确认", "未确定"} else []


def _hospital_bag_confirmation_questions(context: dict[str, Any]) -> list[str]:
    questions = [
        "准生证/户口本：确认医院是否要求携带原件和复印件。",
        "纸尿裤/宝宝衣物：确认医院是否提供，避免重复携带。",
        "胎监带：确认医院是否要求自带，以及需要几条。",
        "陪产/探视：确认是否允许陪产或探视，以及陪产人是否可以过夜。",
        "水和零食：确认产房和病区是否允许携带。",
    ]
    if context.get("birth_path") == "剖宫产":
        questions.append("收腹带/术后用品：剖宫产先问医生或医院是否建议准备。")
    if context.get("feeding_intention") == "配方":
        questions.append("奶瓶/配方奶：确认医院是否允许携带，或是否由医院提供。")
    elif context.get("feeding_intention") in {"母乳", "混合", "纯泵奶"}:
        questions.append("母乳喂养支持：确认医院是否有产后哺乳指导或泌乳顾问资源。")
    if context.get("stage") in {"packing", "immediate"}:
        questions.append("住院时长/出院要求：确认预计住院几天，以及宝宝出院衣物是否有要求。")
    return _dedupe_strings(questions)


def _hospital_bag_focus_items(stage: str, context: dict[str, Any]) -> list[str]:
    base = ["身份证件", "医保卡/保险卡", "产检资料", "手机充电线和充电器", "宝宝出院衣物和包被"]
    if stage in {"packing", "immediate"}:
        base.insert(3, "产褥垫/产妇卫生巾")
        base.insert(4, "一次性内裤")
    if context.get("feeding_intention") in {"母乳", "混合", "纯泵奶"}:
        base.append("哺乳文胸/防溢乳垫")
    return base[:7]


def _hospital_bag_personalized_notes(context: dict[str, Any], form_data: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    stage_labels = {
        "planning": "现在更适合先把医院要求和大方向定下来，不用急着一次买齐。",
        "purchase": "这个阶段可以开始集中准备基础物品，但医院会提供的东西先别重复买。",
        "packing": "已经接近实际打包阶段，清单优先保留能直接装包的物品。",
        "immediate": "现在最重要的是随手能拿走，先保留证件、妈妈护理和宝宝出院基础物品。",
    }
    notes.append(stage_labels.get(context.get("stage"), stage_labels["purchase"]))
    if context.get("birth_path") == "剖宫产":
        notes.append("你选择了剖宫产，所以保留了更宽松、方便拿取和术后更友好的物品提醒。")
    if context.get("feeding_intention") in {"母乳", "混合", "纯泵奶"}:
        notes.append("你有母乳喂养意向，所以保留哺乳文胸、防溢乳垫和吸奶器备用项。")
    elif context.get("feeding_intention") == "配方":
        notes.append("你选择配方喂养，所以奶瓶和配方奶先放到医院确认项里，不默认当作必带。")
    if _first_text(form_data.get("pregnancy_history_or_notes")):
        notes.append("你填写的特殊注意事项只用于打包和医院确认提醒，不做医学判断。")
    return notes[:3]


def _hospital_bag_timeline(stage: str) -> list[str]:
    if stage == "planning":
        return ["下次产检前：先问医院入院材料和提供物品。", "32 周前后：再把基础母婴用品补齐。"]
    if stage == "purchase":
        return ["这两周：先买齐妈妈护理、宝宝出院和证件收纳用品。", "35-36 周：把主包和证件袋实际装好。"]
    if stage == "packing":
        return ["今天或本周：把证件袋、妈妈包和宝宝出院包分开装好。", "出发前：只复核手机、充电线、证件和医院联系信息。"]
    return ["现在：证件、手机、妈妈护理和宝宝出院物品先放固定位置。", "出发前：联系医院或医生，确认入院入口和需要携带的材料。"]


def _hospital_bag_stage(due_date_or_week: str, generation_mode: str, inputs: RuntimeInputs) -> str:
    if generation_mode == "immediate":
        return "immediate"
    week = _pregnancy_week(due_date_or_week, inputs)
    if week is None:
        return "purchase"
    if week >= 37:
        return "immediate"
    if week >= 36:
        return "packing"
    if week >= 32:
        return "purchase"
    return "planning"


def _pregnancy_week(value: str, inputs: RuntimeInputs) -> int | None:
    text = str(value or "")
    match = re.search(r"(\d{1,2})\s*(?:周|w|week)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    date_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not date_match:
        return None
    due_date = _safe_date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
    if due_date is None:
        return None
    today = _message_date(inputs)
    gestational_days = 280 - (due_date - today).days
    if gestational_days < 0:
        return None
    return max(1, min(42, gestational_days // 7))


def _message_date(inputs: RuntimeInputs) -> date:
    raw = str(inputs.get("message_sent_at") or "")
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return date.today()


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _normalize_hospital_bag_form_value(field_id: str, value: str) -> str:
    if field_id == "due_date_or_week":
        return _normalize_hospital_bag_due_or_week(value)
    if field_id == "birth_path":
        return _normalize_hospital_bag_birth_path(value) or value
    if field_id == "feeding_intention":
        return _normalize_hospital_bag_feeding_form_value(value) or value
    if field_id == "first_birth":
        return _normalize_first_birth(value) or value
    if field_id == "fetus_count":
        return _normalize_hospital_bag_fetus_count(value) or value
    if field_id == "support_person":
        return _normalize_hospital_bag_support_person(value) or value
    if field_id == "pregnancy_history_or_notes":
        return _normalize_hospital_bag_history_note(value) or value
    return value


def _normalize_hospital_bag_default_value(field_id: str, value: str) -> str | None:
    normalized_value = _normalize_hospital_bag_form_value(field_id, value)
    if not _has_meaningful_value(normalized_value):
        return None
    if _is_valid_hospital_bag_default_value(field_id, normalized_value):
        return normalized_value
    return None


def _normalize_hospital_bag_default_values(default_values: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    known_field_ids = {field["id"] for field in HOSPITAL_BAG_FORM_FIELDS}
    for field_id in known_field_ids:
        if field_id not in default_values:
            continue
        raw_value = default_values.get(field_id)
        if isinstance(raw_value, list):
            values = [
                normalized_item
                for item in raw_value
                if (normalized_item := _normalize_hospital_bag_default_value(field_id, str(item).strip())) is not None
            ]
            if values:
                normalized[field_id] = values
            continue
        value = _first_text(raw_value)
        normalized_value = _normalize_hospital_bag_default_value(field_id, value)
        if normalized_value is not None:
            normalized[field_id] = normalized_value
    return normalized


def _is_valid_hospital_bag_default_value(field_id: str, value: str) -> bool:
    if field_id == "due_date_or_week":
        return bool(_normalize_hospital_bag_due_or_week(value))

    options = _hospital_bag_field_options(field_id)
    if options and value in options:
        return True

    if field_id == "return_to_work_timing":
        return _looks_like_return_to_work_timing(value)
    if field_id == "top_worries":
        return _looks_like_hospital_bag_worry(value)
    if field_id == "pregnancy_history_or_notes":
        return _looks_like_pregnancy_history_note(value)
    return not options


def _hospital_bag_field_options(field_id: str) -> set[str]:
    for field in HOSPITAL_BAG_FORM_FIELDS:
        if field.get("id") == field_id:
            options = field.get("options")
            if isinstance(options, list):
                return {str(option) for option in options}
            return set()
    return set()


def _normalize_hospital_bag_birth_path(value: str) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if any(token in text for token in ("剖宫产", "剖腹产", "刨腹产", "计划剖", "可能剖")) or any(
        token in lowered for token in ("c_section", "c-section", "cesarean")
    ):
        return "剖宫产"
    if any(token in text for token in ("顺产", "自然分娩")) or any(token in lowered for token in ("vaginal", "natural")):
        return "顺产"
    if any(token in text for token in ("还不确定", "还没确定", "未确定", "不确定")) or "unknown" in lowered:
        return "还不确定"
    return _normalize_birth_path(value)


def _normalize_hospital_bag_due_or_week(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    week_match = re.search(
        r"(孕\s*)?(\d{1,2})\s*(?:周|週|w|week)(?!\s*(?:后|後|以后|以後))(?:\s*[+＋]\s*(\d)\s*(?:天|d)?)?",
        text,
        re.IGNORECASE,
    )
    if week_match:
        week = int(week_match.group(2))
        days = int(week_match.group(3) or 0)
        if not 1 <= week <= 42 or not 0 <= days <= 6:
            return ""
        matched_text = week_match.group(0).strip()
        if matched_text == text:
            return text
        return f"孕{week}周" + (f"+{days}天" if days else "")

    date_match = re.search(
        r"(?:预产期\s*(?:是|在|:|：)?\s*)?((?:20\d{2})[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?)",
        text,
    )
    if not date_match:
        return ""
    date_text = date_match.group(1).replace(" ", "")
    if re.match(r"20\d{2}[/-]\d{1,2}[/-]\d{1,2}$", date_text):
        year, month, day = [int(part) for part in re.split(r"[/-]", date_text)]
        if _safe_date(year, month, day) is None:
            return ""
    if date_match.group(0).strip() == text:
        return text
    return f"预产期{date_text}"


def _looks_like_return_to_work_timing(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(token in text for token in ("返工", "复工", "上班", "工作", "产假", "周后", "个月", "月后", "暂不", "不返工"))


def _looks_like_hospital_bag_worry(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text == "其它":
        return False
    return any(token in text for token in ("怕", "担心", "焦虑", "不舒服", "疼", "漏", "母乳", "没人", "宝宝", "住院", "去医院", "剖宫产", "恢复"))


def _looks_like_pregnancy_history_note(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text == "其它":
        return False
    return any(token in text for token in ("没有", "医生", "妊娠", "糖尿病", "血压", "子痫", "胎盘", "早产", "NICU", "nicu", "高危", "过敏"))


def _normalize_birth_plan_form_value(field_id: str, value: Any) -> Any:
    if field_id == "birth_path":
        raw_text = _first_nonempty_text(value)
        if any(token in raw_text for token in ("还不确定", "还没确定", "未确定", "不确定")):
            return "还没确定"
        if _normalized_placeholder(raw_text) in PLACEHOLDER_VALUES:
            return ""
        normalized = _normalize_birth_path(raw_text)
        if normalized == "未确定" or any(token in raw_text for token in ("还不确定", "还没确定", "未确定", "不确定")):
            return "还没确定"
        return normalized or raw_text
    if field_id == "first_birth":
        normalized = _normalize_first_birth(_first_text(value))
        if normalized == "不确定":
            return "还没确定"
        return normalized or _first_text(value)
    if field_id == "feeding_intention":
        return _normalize_birth_plan_feeding_intention(_first_text(value))
    if isinstance(value, list):
        return [str(item).strip() for item in value if _has_meaningful_value(item)]
    return _first_text(value)


def _normalize_birth_plan_feeding_intention(value: str) -> str:
    feeding = _normalize_feeding_intention(value)
    if feeding in {"母乳", "纯泵奶"}:
        return "母乳喂养"
    if feeding == "混合":
        return "母乳和配方奶都可能"
    if feeding == "配方":
        return "配方奶"
    if feeding == "未确定":
        return "还没想好"
    return value


def _normalize_hospital_bag_feeding_form_value(value: str) -> str:
    feeding = _normalize_feeding_intention(value)
    if feeding == "母乳":
        return "亲喂母乳"
    if feeding == "混合":
        return "混合喂养"
    if feeding == "纯泵奶":
        return "亲喂母乳"
    if feeding == "配方":
        return "配方奶"
    if feeding == "未确定":
        return "还不确定"
    return value


def _normalize_hospital_bag_fetus_count(value: str) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if any(token in text for token in ("三胎及以上", "三胎", "三胞胎", "多胎")) or "triplet" in lowered:
        return "三胎及以上"
    if any(token in text for token in ("双胎", "双胞胎")) or "twin" in lowered:
        return "双胎"
    if "单胎" in text or "singleton" in lowered:
        return "单胎"
    if text in {"不确定", "还不确定", "未确定", "还没确定", "unknown"}:
        return "不确定"
    return value


def _normalize_hospital_bag_support_person(value: str) -> str:
    text = str(value or "").strip()
    if any(token in text for token in ("白天主要自己", "白天自己")):
        return "白天主要自己"
    if any(token in text for token in ("夜间主要自己", "夜间自己", "夜里自己")):
        return "夜间主要自己"
    if any(token in text for token in ("支持少", "没人帮", "没人照顾", "一个人", "主要自己")):
        return "支持少"
    if any(token in text for token in ("有人全天帮忙", "全天", "老公", "丈夫", "伴侣", "妈妈", "婆婆", "家人", "有人帮", "有人陪", "陪我")):
        return "有人全天帮忙"
    if text in {"不确定", "还不确定", "未确定", "暂时没有支持人", "还没确定", "unknown"}:
        return "不确定"
    return value


def _normalize_hospital_bag_history_note(value: str) -> str:
    text = str(value or "").strip()
    if any(token in text for token in ("没有特殊情况", "医生没有提示", "医生没说特殊", "没有高危")):
        return "没有"
    if "妊娠糖尿病" in text:
        return "妊娠糖尿病"
    if "血压" in text or "子痫" in text:
        return "血压或子痫前期风险"
    if "胎盘" in text:
        return "胎盘问题"
    if "早产" in text:
        return "早产风险"
    if "nicu" in text.lower():
        return "宝宝可能 NICU"
    return value


def _normalize_feeding_intention(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if any(token in text for token in ("混合", "combo", "mixed")):
        return "混合"
    if any(token in text for token in ("纯泵", "泵奶", "吸奶", "exclusive pumping", "pumping")):
        return "纯泵奶"
    if any(token in text for token in ("配方", "奶粉", "formula")):
        return "配方"
    if any(token in text for token in ("母乳", "亲喂", "breast")):
        return "母乳"
    if text in {"未确定", "不确定", "还不确定", "还没想好", "unknown"}:
        return "未确定"
    return value


def _normalize_first_birth(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"是", "第一胎", "一胎", "first", "yes", "true"}:
        return "是"
    if text in {"否", "不是", "二胎", "多胎", "no", "false"}:
        return "否"
    if text:
        return "不确定" if text in {"不确定", "未知", "还没确定", "unknown"} else value
    return ""


def _split_hospital_provided_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_normalize_hospital_provided_item(item) for item in value if _normalize_hospital_provided_item(item)]
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"[、,，;；\n]+", text)
    return [_normalize_hospital_provided_item(part) for part in parts if _normalize_hospital_provided_item(part)]


def _normalize_hospital_provided_item(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(医院|院方|病区|产房|月子中心)?\s*(会|已|已经|可能)?\s*(提供|准备|配有|有)\s*", "", text)
    return text.strip()


def _filter_provided_items(items: list[dict[str, Any]], provided_items: list[str]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in items:
        if _is_hospital_provided(item.get("label"), provided_items):
            continue
        filtered.append(item)
    return filtered


def _is_hospital_provided(label: Any, provided_items: list[str]) -> bool:
    label_text = str(label or "")
    if not label_text or not provided_items:
        return False
    label_key = _item_match_key(label_text)
    for provided in provided_items:
        provided_key = _item_match_key(provided)
        if not provided_key:
            continue
        if provided_key in label_key or label_key in provided_key:
            return True
        for alias in HOSPITAL_BAG_PROVIDED_ITEM_ALIASES.get(provided_key, ()):
            alias_key = _item_match_key(alias)
            if alias_key and (alias_key in label_key or label_key in alias_key):
                return True
    return False


def _item_match_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _meaningful_pregnancy_history(context: dict[str, Any]) -> bool:
    values = _text_list(context.get("pregnancy_history_or_notes"))
    return any("没有" not in value for value in values)


def _support_is_limited(context: dict[str, Any]) -> bool:
    value = str(context.get("support_person") or "")
    return any(token in value for token in ("白天主要自己", "夜间主要自己", "支持少", "暂时没有", "不需要", "没有"))


def _support_person_needs_bag(context: dict[str, Any]) -> bool:
    value = str(context.get("support_person") or "")
    if any(token in value for token in ("白天主要自己", "夜间主要自己", "支持少", "暂时没有", "不需要", "没有", "不确定")):
        return False
    return any(token in value for token in ("有人全天帮忙", "陪产", "支持人", "伴侣", "家人", "有，", "有人"))


def _postpartum_pad_quantity(context: dict[str, Any]) -> str:
    if context.get("birth_path") == "剖宫产" or str(context.get("expected_stay")) in {"4 天或以上"}:
        return "20片左右"
    return "10-20片"


def _baby_quantity(context: dict[str, Any], default: str) -> str:
    fetus_count = str(context.get("fetus_count") or "")
    if fetus_count == "双胎":
        return default.replace("1套", "2套").replace("1条", "2条").replace("1-2套", "2-3套").replace("1-2包", "2-3包").replace("各1-2件", "各2份")
    if fetus_count == "三胎及以上":
        return default.replace("1套", "按宝宝数各1套").replace("1条", "按宝宝数各1条").replace("1-2套", "按宝宝数各1套+备用").replace("1-2包", "按宝宝数上调").replace("各1-2件", "按宝宝数各1份")
    return default


def _early_return_to_work(context: dict[str, Any]) -> bool:
    value = str(context.get("return_to_work_timing") or "")
    if not value:
        return False
    if any(token in value for token in ("暂不", "不返工", "半年", "6个月", "六个月", "一年", "较晚")):
        return False
    return bool(re.search(r"(?:[168]|一|六|八)\s*周|(?:[12]|一|两)\s*个?月", value))


def _limit_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return items[:limit]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _apply_hospital_bag_item_explanations(groups: list[Any]) -> None:
    for group in groups:
        if not isinstance(group, dict):
            continue
        items = group.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or _first_text(item.get("explain")):
                continue
            explanation = _hospital_bag_item_explanation(item.get("label"))
            if explanation:
                item["explain"] = explanation


def _hospital_bag_item_explanation(label: Any) -> str:
    label_text = _first_text(label)
    if not label_text:
        return ""
    for tokens, explanation in HOSPITAL_BAG_ITEM_EXPLANATIONS:
        if any(token in label_text for token in tokens):
            return explanation
    return ""


def _normalized_placeholder(value: str) -> str:
    return str(value or "").strip().lower()


def _without_field_help_text(field: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in field.items() if key != "help_text"}


def _sanitize_birth_plan_form_field(field: dict[str, Any]) -> dict[str, Any]:
    if field.get("type") != "multi_select":
        return field
    options = field.get("options")
    if not isinstance(options, list):
        return field
    sanitized = [option for option in options if str(option) not in EXCLUSIVE_BIRTH_PLAN_MULTI_SELECT_OPTIONS]
    return {**field, "options": sanitized}


def _prepare_hospital_bag_card(card_json: dict[str, Any], inputs: RuntimeInputs) -> dict[str, str] | None:
    title = str(card_json.get("title") or "").strip()
    if not title or title == "Hospital Bag Card" or ("待产包" in title and "卡片" in title):
        card_json["title"] = "待产包"

    groups = card_json.get("packing_groups")
    if not isinstance(groups, list):
        groups = []
        card_json["packing_groups"] = groups
    _normalize_hospital_bag_scene_groups(groups)
    _suppress_hospital_bag_personalization(groups)
    _apply_hospital_bag_item_explanations(groups)

    if _formula_only_feeding_intention(inputs):
        return _hospital_bag_cart_followup(card_json)

    group = _find_lactation_or_postpartum_group(groups)
    if group is None:
        group = {"group_id": "postpartum_home_first_week", "title": "产后回家第一周用品", "items": []}
        groups.append(group)

    items = group.get("items")
    if not isinstance(items, list):
        items = []
        group["items"] = items

    _ensure_breast_pump_visible(items)
    _suppress_hospital_bag_personalization(groups)
    _apply_hospital_bag_item_explanations(groups)

    return _hospital_bag_cart_followup(card_json)


def _hospital_bag_cart_followup(card_json: dict[str, Any]) -> dict[str, str]:
    special_logic = _hospital_bag_special_item_logic_summary(card_json)
    message_parts = ["待产包清单我整理好了。"]
    if special_logic:
        message_parts.append(special_logic)
    else:
        message_parts.append("我只保留和孕周、喂养、医院确认真正相关的非常规物品；不确定的先放在需要确认的项目里。具体可以看下面的待产包清单。")
    message_parts.append(
        "我也把适合放入购物车参考的妈妈/宝宝用品整理好了，不用一次买完，先看清单里的优先级，按实际情况删减后再决定是否购买。"
    )
    message_parts.append(f"**{HOSPITAL_BAG_CART_LINK}**")
    return {
        "kind": "hospital_bag_cart",
        "message": "\n\n".join(message_parts),
    }


HOSPITAL_BAG_SPECIAL_LOGIC_FIELD_ORDER = [
    "birth_path",
    "feeding_intention",
    "return_to_work_timing",
    "fetus_count",
    "pregnancy_history_or_notes",
    "top_worries",
    "support_person",
]

HOSPITAL_BAG_SPECIAL_LOGIC_ITEM_ORDER: dict[str, list[str]] = {
    "birth_path": ["高腰宽松内裤", "不压腹出院裤/裙", "收腹带"],
    "feeding_intention": ["哺乳文胸/哺乳背心", "防溢乳垫", "便携式吸奶器", "储奶袋/储奶瓶", "乳头霜", "乳盾", "奶瓶", "配方奶"],
    "return_to_work_timing": ["冷藏包/冰袋", "储奶袋/储奶瓶", "吸奶配件清洁包", "便携式吸奶器", "标签笔"],
    "fetus_count": ["宝宝出院衣物", "备用连体衣", "包被", "小毯子", "纸尿裤", "湿巾/棉柔巾", "帽子/袜子", "安全提篮/安全座椅"],
    "pregnancy_history_or_notes": ["血糖记录/饮食医嘱", "医生允许的加餐", "血压记录/用药清单", "近期B超/医生医嘱", "NICU探视/送奶规则确认", "小码/早产儿衣物确认", "标签笔"],
    "top_worries": ["便携式吸奶器", "乳头霜", "乳盾", "宝宝出院衣物", "备用连体衣", "包被", "医院路线和停车信息", "夜间入口信息"],
    "support_person": ["床边收纳袋", "陪产人身份证件", "手机充电器", "充电宝", "换洗衣物", "外套", "洗漱用品"],
}


def _hospital_bag_special_item_logic_summary(card_json: dict[str, Any]) -> str:
    entries_by_field = _hospital_bag_special_item_entries_by_field(card_json)
    bullets: list[str] = []
    for field in HOSPITAL_BAG_SPECIAL_LOGIC_FIELD_ORDER:
        entries = entries_by_field.get(field) or []
        if not entries:
            continue
        sentence = _hospital_bag_special_item_logic_sentence(field, entries)
        if sentence:
            bullets.append(f"- {sentence}")
        if len(bullets) >= 3:
            break
    if not bullets:
        return ""
    return "特殊物品我按这几个情况做了取舍：\n" + "\n".join(bullets) + "\n具体可以看下面的待产包清单。"


def _hospital_bag_special_item_entries_by_field(card_json: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    entries_by_field: dict[str, list[dict[str, str]]] = {}
    groups = card_json.get("packing_groups")
    if not isinstance(groups, list):
        groups = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        items = group.get("items")
        if not isinstance(items, list):
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            label = _first_text(item.get("label"))
            if not label:
                continue
            priority = _first_text(item.get("priority"))
            sources = item.get("personalized_by")
            if not isinstance(sources, list):
                continue
            for source in sources:
                if isinstance(source, dict):
                    field = _first_text(source.get("field"))
                    if field:
                        entries_by_field.setdefault(field, []).append(
                            {
                                "label": label,
                                "priority": priority,
                                "condition": _first_text(source.get("condition")),
                                "effect": _first_text(source.get("effect")),
                            }
                        )
    return entries_by_field


def _hospital_bag_special_item_logic_sentence(field: str, entries: list[dict[str, str]]) -> str:
    condition = _hospital_bag_first_entry_condition(entries)
    prepared_limit = 3 if field in {"return_to_work_timing", "fetus_count", "pregnancy_history_or_notes", "top_worries"} else 4
    if field == "birth_path":
        prepared_limit = 2
    prepared = _hospital_bag_special_item_labels(field, entries, confirm_first=False, limit=prepared_limit)
    confirm_first = _hospital_bag_special_item_labels(field, entries, confirm_first=True, limit=2)
    if field == "birth_path":
        intro = "考虑到你倾向剖宫产" if "剖" in condition else f"考虑到你的分娩方式是{condition}"
        return _hospital_bag_prepared_sentence(intro, prepared, confirm_first, confirm_text="先放在需要问医生的项目里")
    if field == "feeding_intention":
        intro = f"考虑到你准备{condition or '母乳或混合'}喂养"
        return _hospital_bag_prepared_sentence(intro, prepared, confirm_first)
    if field == "return_to_work_timing":
        intro = f"考虑到你预计{condition}返工" if condition else "考虑到你产后不久要返工"
        return _hospital_bag_prepared_sentence(intro, prepared, confirm_first)
    if field == "fetus_count":
        labels = prepared or confirm_first
        if not labels:
            return ""
        subject = f"这次是{condition}" if condition else "宝宝数量"
        return f"考虑到{subject}，我把{_join_chinese_labels(labels[:3])}的数量按你的情况做了调整。"
    if field == "pregnancy_history_or_notes":
        labels = prepared or confirm_first
        if not labels:
            return ""
        detail = condition or "医生提示"
        return f"考虑到你填写了{detail}，我为你准备了{_join_chinese_labels(labels[:3])}，并把需要按医院规则确认的项目留在清单里。"
    if field == "top_worries":
        labels = prepared or confirm_first
        if not labels:
            return ""
        detail = condition or "最担心的事"
        return f"考虑到你担心{detail}，我为你准备了{_join_chinese_labels(labels[:3])}。"
    if field == "support_person":
        return _hospital_bag_prepared_sentence("考虑到你的支持人安排", prepared, confirm_first)
    return ""


def _hospital_bag_prepared_sentence(intro: str, prepared: list[str], confirm_first: list[str], *, confirm_text: str = "先放在需要确认的项目里") -> str:
    parts: list[str] = []
    if prepared:
        parts.append(f"我为你准备了{_join_chinese_labels(prepared)}")
    if confirm_first:
        parts.append(f"{_join_chinese_labels(confirm_first)}{confirm_text}")
    if not parts:
        return ""
    return f"{intro}，{'，'.join(parts)}。"


def _hospital_bag_special_item_labels(field: str, entries: list[dict[str, str]], *, confirm_first: bool, limit: int) -> list[str]:
    preferred = HOSPITAL_BAG_SPECIAL_LOGIC_ITEM_ORDER.get(field, [])
    preferred_rank = {label: index for index, label in enumerate(preferred)}
    filtered = [
        entry
        for entry in entries
        if (_first_text(entry.get("priority")) == "confirm_first") is confirm_first
    ]
    filtered.sort(key=lambda entry: (preferred_rank.get(_first_text(entry.get("label")), len(preferred_rank)), _first_text(entry.get("label"))))
    labels = _dedupe_strings([_first_text(entry.get("label")) for entry in filtered if _first_text(entry.get("label"))])
    return labels[:limit]


def _hospital_bag_first_entry_condition(entries: list[dict[str, str]]) -> str:
    for entry in entries:
        condition = _first_text(entry.get("condition"))
        if condition:
            return condition
    return ""


def _join_chinese_labels(labels: list[str]) -> str:
    clean = [label for label in labels if label]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return "和".join(clean)
    return "、".join(clean[:-1]) + "和" + clean[-1]


def _prepare_birth_plan_card(card_json: dict[str, Any], inputs: RuntimeInputs, form_data_override: dict[str, Any] | None = None) -> dict[str, str]:
    source = dict(card_json)
    form_data = form_data_override if form_data_override is not None else _confirmed_form_data(inputs)
    owner = _dict_value(source.get("owner"))
    overview = _dict_value(source.get("overview"))
    birth_preferences = _dict_value(source.get("birth_preferences"))
    plan_change_values = _birth_plan_change_values(source.get("if_plans_change"))
    top_priorities = _normalize_birth_plan_items(
        source.get("top_priorities"),
        _nested_text(source, "if_plans_change", "what_matters_most"),
        form_data.get("top_priorities"),
        form_data.get("priority_notes"),
        max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
    )

    compact = {
        "card_type": "birth_plan_card",
        "schema_version": "1.0",
        "title": _localized_birth_plan_title(_first_text(source.get("title"))) or "分娩沟通单",
        "subtitle": _localized_birth_plan_subtitle(_first_text(source.get("subtitle"))) or "产房沟通重点",
        "overview": {
            "due_date_or_week": _first_text(
                overview.get("due_date_or_week"),
                owner.get("due_date_or_week"),
                form_data.get("due_date_or_week"),
            )
            or "待确认",
            "birth_path": _normalize_birth_path(
                _first_text(
                    overview.get("birth_path"),
                    birth_preferences.get("birth_path"),
                    form_data.get("birth_path"),
                )
            )
            or "待确认",
            "birth_setting": _first_text(
                overview.get("birth_setting"),
                owner.get("birth_setting"),
                form_data.get("birth_setting"),
            ),
            "support_people": _first_text(
                overview.get("support_people"),
                overview.get("support_person"),
                owner.get("support_people"),
                owner.get("support_person"),
                form_data.get("support_person"),
                form_data.get("support_people"),
            )
            or "待确认",
        },
        "personalized_notes": _birth_plan_personalized_notes(form_data),
        "top_priorities": top_priorities,
        "communication": _normalize_birth_plan_items(
            source.get("communication"),
            source.get("communication_preferences"),
            form_data.get("communication_preferences"),
            _birth_plan_priority_communication_items(top_priorities),
            _birth_plan_priority_general_items(top_priorities),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "labor_preferences": _normalize_birth_plan_items(
            source.get("labor_preferences"),
            form_data.get("labor_preferences"),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "intervention_preferences": _normalize_birth_plan_items(
            source.get("intervention_preferences"),
            form_data.get("intervention_preferences"),
            _birth_plan_priority_intervention_items(top_priorities),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "pain_relief": _normalize_birth_plan_items(
            source.get("pain_relief"),
            source.get("pain_relief_preferences"),
            form_data.get("pain_relief_preferences"),
            form_data.get("pain_relief_notes"),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "baby_after_birth": _normalize_birth_plan_items(
            source.get("baby_after_birth"),
            source.get("baby_after_birth_preferences"),
            form_data.get("baby_after_birth_preferences"),
            _birth_plan_priority_baby_items(top_priorities),
            _birth_plan_feeding_note(form_data.get("feeding_intention")),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "if_plans_change": _normalize_birth_plan_items(
            *plan_change_values,
            form_data.get("if_plans_change"),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "emergency_authorization": _normalize_birth_plan_items(
            source.get("emergency_authorization"),
            form_data.get("emergency_authorization"),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "questions_for_hospital": _normalize_birth_plan_items(
            source.get("questions_for_hospital"),
            form_data.get("hospital_questions_focus"),
            _birth_plan_default_questions(form_data),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "medical_notes": _normalize_medical_notes(form_data.get("medical_notes")),
        "disclaimer": _localized_disclaimer(_first_text(source.get("disclaimer"))) or BIRTH_PLAN_DISCLAIMER,
    }

    card_json.clear()
    card_json.update(compact)
    return dict(BIRTH_PLAN_ASSISTANT_FOLLOWUP)


def _find_lactation_or_postpartum_group(groups: list[Any]) -> dict[str, Any] | None:
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id", "")).lower()
        title = str(group.get("title", "")).lower()
        if any(token in group_id for token in ("lactation", "breastfeeding", "feeding", "postpartum")):
            return group
        if any(token in title for token in ("lactation", "breastfeeding", "feeding", "postpartum", "哺乳", "喂养", "产后")):
            return group
    return None


def _normalize_hospital_bag_scene_groups(groups: list[Any]) -> None:
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id", "")).lower()
        title = str(group.get("title", ""))
        text = f"{group_id} {title}".lower()
        if any(token in text for token in ("lactation", "breastfeeding", "feeding", "postpartum", "哺乳", "喂养", "产后回家", "产后护理")):
            group["group_id"] = "postpartum_home_first_week"
            group["title"] = "产后回家第一周用品"
        elif any(token in text for token in ("documents", "certificate", "证件", "资料", "文件")):
            group["group_id"] = "documents"
            group["title"] = "证件文件包"
        elif any(token in text for token in ("baby", "宝宝", "新生儿")):
            group["group_id"] = "baby_discharge_bag"
            group["title"] = "宝宝出院包"
        elif any(token in text for token in ("support", "partner", "companion", "陪产", "支持人")):
            group["group_id"] = "support_person_bag"
            group["title"] = "陪产人包"
        elif any(token in text for token in ("car", "travel", "traffic", "transport", "车上", "交通", "停车", "路线")):
            group["group_id"] = "car_backup_bag"
            group["title"] = "车上备用包"
        elif any(token in text for token in ("mom", "mother", "communication", "food", "妈妈", "衣物", "清洁", "护理", "通讯", "饮食", "住院")):
            group["group_id"] = "mom_hospital_bag"
            group["title"] = "妈妈住院包"


def _ensure_breast_pump_visible(items: list[Any]) -> None:
    pump_index = _breast_pump_item_index(items)
    target_index = min(2, len(items))
    if pump_index is None:
        items.insert(target_index, dict(PUMP_ITEM))


def _breast_pump_item_index(items: list[Any]) -> int | None:
    for index, item in enumerate(items):
        text = json.dumps(item, ensure_ascii=False).lower() if isinstance(item, dict) else str(item).lower()
        if "吸奶" in text or "breast pump" in text or "pump" in text:
            return index
    return None


def _formula_only_feeding_intention(inputs: RuntimeInputs) -> bool:
    data = _confirmed_form_data(inputs)
    if data:
        return _normalize_feeding_intention(_first_text(data.get("feeding_intention"))) == "配方"
    message = str(inputs.get("user_message", ""))
    marker = "confirmed_form_data:"
    if marker not in message:
        return False
    raw_json = message.split(marker, 1)[1].strip()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return False
    return _normalize_feeding_intention(_first_text(data.get("feeding_intention"))) == "配方"


def _confirmed_form_data(inputs: RuntimeInputs) -> dict[str, Any]:
    message = str(inputs.get("user_message", ""))
    marker = "confirmed_form_data:"
    if marker not in message:
        return {}
    raw_json = message.split(marker, 1)[1].strip()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _confirmed_form_data_for_form(inputs: RuntimeInputs, form_id: str) -> dict[str, Any]:
    confirmed_form_id = _confirmed_form_id(inputs)
    if confirmed_form_id != form_id:
        return {}
    return _confirmed_form_data(inputs)


def _confirmed_form_id(inputs: RuntimeInputs) -> str:
    message = str(inputs.get("user_message", ""))
    for line in message.splitlines():
        text = line.strip()
        if text.startswith("form_id:"):
            return text.split(":", 1)[1].strip()
    return ""


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _birth_prep_shared_default_values(inputs: RuntimeInputs, explicit_values: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for source in (
        _active_birth_journey_plan_default_values(inputs),
        _birth_prep_profile_default_values(inputs),
        _dict_value(inputs.get("_birth_prep_hospital_bag_slots")),
        explicit_values or {},
    ):
        defaults.update(_birth_prep_shared_values_from_source(source))
    return defaults


def _birth_prep_shared_values_from_source(source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    age = _first_text(source.get("age"), source.get("birth_prep_age"))
    due = _normalize_birth_prep_shared_due_or_week(
        source.get("due_date_or_week"),
        source.get("birth_prep_due_date_or_week"),
        source.get("due_date"),
        source.get("current_week"),
        source.get("gestational_week"),
    )
    ivf = _first_text(source.get("ivf"), source.get("birth_prep_ivf"), source.get("is_ivf"))
    fetus_count = _first_text(
        source.get("fetus_count"),
        source.get("birth_prep_fetus_count"),
        source.get("baby_count"),
        source.get("singleton_or_multiple"),
    )
    city_or_country = _first_text(
        source.get("city_or_country"),
        source.get("birth_prep_city_or_country"),
        source.get("region"),
        source.get("city"),
        source.get("country"),
        source.get("location"),
    )
    birth_hospital = _first_text(
        source.get("birth_hospital"),
        source.get("birth_prep_birth_hospital"),
        source.get("birth_setting"),
        source.get("hospital"),
        source.get("registered_hospital"),
    )
    birth_path = _normalize_birth_prep_shared_birth_path(
        source.get("birth_path"),
        source.get("birth_prep_birth_path"),
        source.get("delivery_method"),
        source.get("birth_method"),
        source.get("planned_birth_method"),
        source.get("delivery_mode"),
    )
    first_birth = _normalize_first_birth(_first_text(source.get("first_birth"), source.get("birth_prep_first_birth")))
    feeding_intention = _normalize_feeding_intention(
        _first_text(source.get("feeding_intention"), source.get("birth_prep_feeding_intention"), source.get("feeding_plan"))
    )
    if feeding_intention == "母乳":
        feeding_intention = "亲喂母乳"
    elif feeding_intention == "混合":
        feeding_intention = "混合喂养"
    elif feeding_intention == "配方":
        feeding_intention = "配方奶"
    elif feeding_intention == "未确定":
        feeding_intention = "还不确定"
    return_to_work_timing = _first_text(
        source.get("return_to_work_timing"),
        source.get("birth_prep_return_to_work_timing"),
        source.get("maternity_leave"),
    )
    support_person = _first_text(
        source.get("support_person"),
        source.get("birth_prep_support_person"),
        source.get("support_people"),
        source.get("partner_or_support"),
        source.get("primary_support_person"),
    )
    pregnancy_history_or_notes = _first_text(
        source.get("pregnancy_history_or_notes"),
        source.get("birth_prep_pregnancy_history_or_notes"),
        source.get("medical_notes"),
        source.get("special_notes"),
        source.get("doctor_notes"),
        source.get("risk_factors"),
        source.get("high_risk_factors"),
    )
    top_worries = _first_text(source.get("top_worries"), source.get("birth_prep_top_worries"))
    defaults: dict[str, Any] = {}
    if _has_meaningful_value(age):
        defaults["age"] = age
    if _has_meaningful_value(due):
        defaults["due_date_or_week"] = due
        current_week = _birth_journey_default_current_week({"due_date_or_week": due})
        if current_week:
            defaults["current_week"] = current_week
    if _has_meaningful_value(ivf):
        defaults["ivf"] = ivf
    if _has_meaningful_value(fetus_count):
        defaults["fetus_count"] = fetus_count
    if _has_meaningful_value(city_or_country):
        defaults["city_or_country"] = city_or_country
    if _has_meaningful_value(birth_hospital):
        defaults["birth_hospital"] = birth_hospital
        defaults["birth_setting"] = birth_hospital
    if birth_path:
        defaults["birth_path"] = birth_path
    if _has_meaningful_value(first_birth):
        defaults["first_birth"] = first_birth
    if _has_meaningful_value(feeding_intention):
        defaults["feeding_intention"] = feeding_intention
    if _has_meaningful_value(return_to_work_timing):
        defaults["return_to_work_timing"] = return_to_work_timing
    if _has_meaningful_value(support_person):
        defaults["support_person"] = support_person
    if _has_meaningful_value(pregnancy_history_or_notes):
        defaults["pregnancy_history_or_notes"] = pregnancy_history_or_notes
    if _has_meaningful_value(top_worries):
        defaults["top_worries"] = top_worries
    return defaults


def _normalize_birth_prep_shared_due_or_week(*values: Any) -> str:
    for value in values:
        text = _first_text(value)
        if not text:
            continue
        normalized = _normalize_hospital_bag_due_or_week(text)
        if normalized:
            return normalized
        current_week = _birth_journey_default_current_week({"current_week": text})
        if current_week:
            return current_week
    return ""


def _normalize_birth_prep_shared_birth_path(*values: Any) -> str:
    text = _first_nonempty_text(*values)
    if not text:
        return ""
    return _normalize_hospital_bag_birth_path(text) or _normalize_birth_journey_birth_path(text) or _normalize_birth_path(text) or text


def _active_birth_journey_plan_default_values(inputs: RuntimeInputs) -> dict[str, Any]:
    user_id = _birth_prep_runtime_user_id(inputs)
    if not user_id:
        return {}
    try:
        active_plans = data_store.list_care_plan_artifacts(user_id=user_id, status="active")
    except Exception:
        return {}
    birth_journey_plan = next(
        (plan for plan in active_plans if isinstance(plan, dict) and plan.get("plan_type") == "birth_journey"),
        None,
    )
    if not isinstance(birth_journey_plan, dict):
        return {}
    payload = birth_journey_plan.get("payload") if isinstance(birth_journey_plan.get("payload"), dict) else {}
    owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
    overview = payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
    birth_preferences = payload.get("birth_preferences") if isinstance(payload.get("birth_preferences"), dict) else {}
    return _birth_prep_shared_values_from_source(
        {
            "due_date_or_week": _first_text(overview.get("due_date_or_week"), owner.get("due_date_or_week")),
            "fetus_count": _first_text(overview.get("fetus_count"), owner.get("fetus_count")),
            "birth_hospital": _first_text(overview.get("birth_setting"), owner.get("birth_setting")),
            "birth_path": _first_text(overview.get("birth_path"), birth_preferences.get("birth_path"), owner.get("birth_path")),
            "feeding_intention": _first_text(overview.get("feeding_intention"), owner.get("feeding_intention")),
            "support_person": _first_text(overview.get("support_person"), owner.get("support_person")),
        }
    )


def _birth_prep_profile_default_values(inputs: RuntimeInputs) -> dict[str, Any]:
    profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    return _birth_prep_shared_values_from_source(profile)


def _birth_prep_runtime_user_id(inputs: RuntimeInputs) -> str:
    user_profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    return str(inputs.get("user_id") or user_profile.get("user_id") or "").strip()


def _persist_birth_prep_profile_memory(inputs: RuntimeInputs, values: dict[str, Any]) -> None:
    user_profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    user_id = str(inputs.get("user_id") or user_profile.get("user_id") or "").strip()
    if not user_id:
        return
    shared_values = _birth_prep_shared_values_from_source(values)
    due = _first_text(shared_values.get("due_date_or_week"), values.get("due_date"), values.get("current_week"))
    birth_path = _normalize_birth_path(_first_text(values.get("birth_path"), values.get("delivery_method")))
    if not birth_path:
        birth_path = _first_text(shared_values.get("birth_path"))
    support_person = _first_text(shared_values.get("support_person"), values.get("support_people"), values.get("partner_or_support"))
    if not any(_has_meaningful_value(value) for value in shared_values.values()):
        return
    update_kwargs = {
        "user_id": user_id,
        "age": shared_values.get("age"),
        "due_date_or_week": due,
        "ivf": shared_values.get("ivf"),
        "fetus_count": shared_values.get("fetus_count"),
        "city_or_country": shared_values.get("city_or_country"),
        "birth_hospital": shared_values.get("birth_hospital"),
        "birth_path": birth_path,
        "first_birth": shared_values.get("first_birth"),
        "feeding_intention": shared_values.get("feeding_intention"),
        "return_to_work_timing": shared_values.get("return_to_work_timing"),
        "support_person": support_person,
        "pregnancy_history_or_notes": shared_values.get("pregnancy_history_or_notes"),
        "top_worries": shared_values.get("top_worries"),
    }
    profile_patch = _birth_prep_profile_runtime_patch(update_kwargs)
    if profile_patch:
        inputs["user_profile"] = {**user_profile, **profile_patch}
        inputs["_user_profile_loaded_from_db"] = True
    try:
        profile_write_queue.enqueue_birth_prep_profile_update(**update_kwargs)
    except Exception:
        return


def _birth_prep_profile_runtime_patch(values: dict[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    age = _runtime_age_value(values.get("age"))
    if age is not None:
        patch["age"] = age
    field_map = {
        "due_date_or_week": "birth_prep_due_date_or_week",
        "ivf": "birth_prep_ivf",
        "fetus_count": "birth_prep_fetus_count",
        "city_or_country": "birth_prep_city_or_country",
        "birth_hospital": "birth_prep_birth_hospital",
        "birth_path": "birth_prep_birth_path",
        "first_birth": "birth_prep_first_birth",
        "feeding_intention": "birth_prep_feeding_intention",
        "return_to_work_timing": "birth_prep_return_to_work_timing",
        "support_person": "birth_prep_support_person",
        "pregnancy_history_or_notes": "birth_prep_pregnancy_history_or_notes",
        "top_worries": "birth_prep_top_worries",
    }
    for source_key, profile_key in field_map.items():
        text = _first_text(values.get(source_key))
        if text:
            patch[profile_key] = text
    return patch


def _runtime_age_value(value: Any) -> int | None:
    text = _first_text(value)
    if not text:
        return None
    try:
        age = int(float(text))
    except Exception:
        return None
    if age < 0 or age > 120:
        return None
    return age


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            text = ", ".join(str(item) for item in value if _has_meaningful_value(item))
        elif isinstance(value, dict):
            text = ", ".join(
                str(nested_value)
                for nested_value in value.values()
                if _has_meaningful_value(nested_value)
            )
        else:
            text = str(value or "").strip()
        if _has_meaningful_value(text):
            return text
    return ""


def _first_nonempty_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if str(item or "").strip())
        elif isinstance(value, dict):
            text = ", ".join(str(nested_value).strip() for nested_value in value.values() if str(nested_value or "").strip())
        else:
            text = str(value or "").strip()
        if text:
            return text
    return ""


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_text_list(item))
        return items
    if isinstance(value, dict):
        items: list[str] = []
        for item in value.values():
            items.extend(_text_list(item))
        return items
    text = str(value or "").strip()
    if not _has_meaningful_value(text):
        return []
    parts = re.split(r"[、,，;；\n]+", text)
    return [part.strip() for part in parts if _has_meaningful_value(part)]


def _localized_disclaimer(value: str) -> str:
    if not value:
        return ""
    normalized = value.strip().lower()
    if "this card is for communication only" in normalized or "clinician and hospital guidance" in normalized:
        return BIRTH_PLAN_DISCLAIMER
    return value


def _localized_birth_plan_title(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"birth plan card", "labor room communication priority card"}:
        return "分娩沟通单"
    return value


def _localized_birth_plan_subtitle(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"birth plan card", "labor room communication priority card"}:
        return "产房沟通重点"
    return value


def _normalize_birth_path(value: str) -> str:
    text = value.strip()
    if not _has_meaningful_value(text):
        return ""
    return BIRTH_PATH_ALIASES.get(text.lower()) or BIRTH_PATH_ALIASES.get(text) or text


def _nested_text(source: dict[str, Any], *keys: str) -> str:
    current: Any = source
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return _first_text(current)


def _birth_plan_change_values(value: Any) -> list[Any]:
    if not isinstance(value, dict):
        return [value]
    return [value.get("how_to_explain_changes"), value.get("who_should_be_involved")]


def _birth_plan_priority_communication_items(items: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in items:
        if "伴侣" in item or "支持人" in item:
            mapped.append("重要决定也请同步伴侣/支持人")
        if "鼓励" in item or "反馈" in item:
            mapped.append("希望团队主动给我反馈和鼓励")
        if "减少不必要的干预" in item or "不必要的干预" in item or "非必要操作" in item:
            mapped.append("干预前请先和我沟通必要性")
    return mapped


def _birth_plan_priority_baby_items(items: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in items:
        if "肌肤接触" in item or "抱一抱" in item or "贴一贴" in item:
            mapped.append("出生后希望尽早肌肤接触")
        if "尝试母乳" in item or "尽早母乳" in item or "喂母乳" in item or "亲喂" in item:
            mapped.append("希望尽早尝试母乳")
    return mapped


def _birth_plan_priority_intervention_items(items: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in items:
        if any(token in item for token in ("侧切", "产钳", "真空吸引", "人工破膜")):
            mapped.append(item)
    return mapped


def _birth_plan_priority_general_items(items: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in items:
        if any(token in item for token in ("肌肤接触", "抱一抱", "贴一贴", "尝试母乳", "尽早母乳", "喂母乳", "亲喂", "侧切", "产钳", "真空吸引", "人工破膜")):
            continue
        if any(token in item for token in ("伴侣", "支持人", "鼓励", "反馈", "减少不必要的干预", "不必要的干预", "非必要操作")):
            continue
        mapped.append(f"希望医护团队知道：{item}")
    return mapped


def _normalize_birth_plan_items(*values: Any, max_items: int) -> list[str]:
    items: list[str] = []
    for value in values:
        for item in _flatten_text_items(value):
            softened = _soften_birth_plan_request(_normalize_birth_plan_text(item))
            if softened and softened not in items:
                items.append(softened)
            if len(items) >= max_items:
                return items
    return items


def _normalize_medical_notes(value: Any) -> list[str]:
    # Medical notes must remain user-supplied facts only; do not derive them from model-generated card fields.
    return _flatten_text_items(value)[:3]


def _birth_plan_personalized_notes(form_data: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    due = _first_text(form_data.get("due_date_or_week"))
    birth_path = _normalize_birth_path(_first_text(form_data.get("birth_path")))
    birth_setting = _first_text(form_data.get("birth_setting"))
    support_people = _first_text(form_data.get("support_person"), form_data.get("support_people"))
    first_birth = _first_text(form_data.get("first_birth"))
    if due or birth_path:
        parts = [part for part in (due, birth_path) if part]
        notes.append(f"基于{'、'.join(parts)}，整理成适合产检或入院沟通的重点内容。")
    if first_birth == "是":
        notes.append("已按第一胎更需要解释、反馈和陪伴的场景整理。")
    elif first_birth == "否":
        notes.append("已按非第一胎保留更关键的沟通重点。")
    if support_people:
        notes.append(f"已把{support_people}作为重要沟通参与人。")
    if birth_setting:
        notes.append(f"可在{birth_setting}产检或入院前给医护团队查看。")
    return notes[:3]


def _birth_plan_default_questions(form_data: dict[str, Any]) -> list[str]:
    questions = [
        "陪产/支持人：确认谁可以参与沟通、陪产或术前/产房决策。",
    ]
    if _normalize_birth_path(_first_text(form_data.get("birth_path"))) == "剖宫产":
        questions.append("术后接触宝宝/喂养：确认安全允许时的肌肤接触、喂养和宝宝护理流程。")
    else:
        questions.append("疼痛缓解：确认无痛或麻醉什么时候可以沟通、有哪些选择。")
    return questions


def _birth_plan_feeding_note(value: Any) -> str:
    text = _first_text(value)
    if not _has_meaningful_value(text):
        return ""
    return f"喂养意向：{text}"


def _normalize_birth_plan_text(text: str) -> str:
    normalized = re.sub(r"^\s*\d+[.)、．]\s*", "", text.strip())
    normalized = re.sub(r"\s+", " ", normalized)
    lowered = normalized.lower()
    if lowered in {"skin-to-skin", "skin to skin"}:
        return "出生后尽早肌肤接触"
    if normalized == "我还没想好，请帮我整理成温和版本":
        return "希望医护团队在关键步骤前先解释，并给我一点时间确认。"
    return normalized.replace("skin-to-skin", "出生后尽早肌肤接触").replace("skin to skin", "出生后尽早肌肤接触")


def _flatten_text_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_flatten_text_items(item))
        return items
    if isinstance(value, dict):
        items = []
        for item in value.values():
            items.extend(_flatten_text_items(item))
        return items
    text = str(value).strip()
    if not _has_meaningful_value(text):
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for token in ("；", ";", "\n"):
        normalized = normalized.replace(token, "\n")
    items = []
    for raw_item in normalized.split("\n"):
        item = raw_item.strip(" -•、，,。.")
        if _has_meaningful_value(item):
            items.append(item)
    return items


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    text = str(value).strip()
    return text.lower() not in PLACEHOLDER_VALUES


def _soften_birth_plan_request(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    english_tokens = ("i refuse", "must", "never", "do not", "don't")
    chinese_tokens = ("拒绝", "不要", "不允许", "必须", "一定要")
    strong_tokens = (*english_tokens, *chinese_tokens)
    if not any(token in lowered or token in stripped for token in strong_tokens):
        return stripped

    if "侧切" in stripped:
        return "如果需要侧切，请先说明原因并和我沟通。"
    if "产钳" in stripped or "真空吸引" in stripped or "吸引" in stripped:
        return "如果需要产钳或吸引，请先解释原因并和我沟通。"
    if "人工破水" in stripped or "破水" in stripped:
        return "如果需要人工破水，请先说明原因并和我沟通。"
    if "灌肠" in stripped or "剃毛" in stripped:
        return "如果需要灌肠或剃毛，请先告诉我是否必须。"
    if "无痛" in stripped or "硬膜外" in stripped or "麻醉" in stripped:
        return "关于无痛或麻醉选择，请先说明可选方案、时机和注意事项。"

    softened = stripped
    for token in strong_tokens:
        softened = softened.replace(token, "").replace(token.capitalize(), "")
    softened = softened.strip(" ，,。.:：;；")
    if not softened:
        softened = "这项偏好"
    return f"在{softened}前，请先和我沟通。"


def _normalize_form_fields(raw_fields: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_fields, list):
        return []

    fields: list[dict[str, Any]] = []
    for raw_field in raw_fields:
        field = raw_field
        if isinstance(raw_field, str):
            try:
                parsed = json.loads(raw_field)
            except json.JSONDecodeError:
                continue
            field = parsed
        if not isinstance(field, dict):
            continue

        normalized = {
            "id": str(field.get("id", "")),
            "label": str(field.get("label", "")),
            "type": str(field.get("type", "text")),
            "required": bool(field.get("required", False)),
        }
        if field.get("allow_other_input") is not None:
            normalized["allow_other_input"] = bool(field.get("allow_other_input"))
        for optional_key in ("help_text", "placeholder", "other_placeholder", "default_value"):
            value = field.get(optional_key)
            if value is not None and value != "":
                normalized[optional_key] = value
        options = field.get("options")
        if isinstance(options, list) and options:
            normalized["options"] = [str(option) for option in options]
        if normalized["id"] and normalized["label"]:
            fields.append(normalized)

    return fields
