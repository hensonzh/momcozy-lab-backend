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
        existing_plan = normalize_birth_journey_care_plan_artifact(existing_plan) or existing_plan
        existing_payload = existing_plan.get("payload") if isinstance(existing_plan.get("payload"), dict) else {}
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

    existing_plan = _existing_birth_journey_care_plan(inputs, require_reusable=False)
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
    existing_plan = _existing_birth_journey_care_plan(inputs, require_reusable=False)
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
            "summary": "需要明确要更新哪一项孕期计划待办。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
            "plan_id": int(existing_plan.get("plan_id") or 0),
            "data": {
                "confirmation_question": "你想标记完成的是当前待办里的哪一项？可以告诉我编号或事项名称。",
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
    if _confirmed_form_id(inputs) == "birth_journey_basic_info_intake" and _confirmed_form_data(inputs):
        action = "submit_basic_info"
    intake_state = _normalize_birth_journey_intake_state(_dict_value(inputs.get("_birth_journey_intake_state")))
    if action in {"start", "get_state"} and not intake_state.get("started"):
        intake_state["started"] = True
        _merge_birth_journey_entry_context(intake_state, payload, inputs)

    if action == "submit_basic_info":
        basic_info = _birth_journey_basic_info_payload(payload)
        if basic_info:
            intake_state["basic_info"] = {**_dict_value(intake_state.get("basic_info")), **basic_info}
            intake_state.pop("personalized_followup_queue", None)
            intake_state.pop("active_personalized_followup_id", None)
            _persist_birth_prep_profile_memory(inputs, basic_info)
    elif action == "submit_entry_concern":
        intake_state["entry_concern_followup"] = _birth_journey_text_or_skipped(payload, "entry_concern_followup")
    elif action == "mark_checkup_records_uploaded":
        intake_state["checkup_done_confirmed"] = True
        intake_state["checkup_records_uploaded"] = True
        note = _first_text(payload.get("checkup_status"), payload.get("checkup_note"), payload.get("note"))
        intake_state["checkup_status"] = note or "已上传产检记录，等待 CozyMate 整理。"
    elif action == "skip_checkup_records":
        intake_state["checkup_records_uploaded"] = False
        intake_state["checkup_status"] = "未上传产检记录"
    elif action == "confirm_checkup_done":
        intake_state["checkup_done_confirmed"] = True
        note = _first_text(payload.get("checkup_status"), payload.get("checkup_note"), payload.get("note"))
        if note:
            intake_state["checkup_done_note"] = note
    elif action == "confirm_no_checkup_yet":
        intake_state["checkup_done_confirmed"] = False
        intake_state["checkup_records_uploaded"] = False
        note = _first_text(payload.get("checkup_status"), payload.get("checkup_note"), payload.get("note"))
        intake_state["checkup_status"] = note or "还没做过产检"
    elif action == "confirm_ready_to_generate":
        intake_state["final_plan_confirmed"] = True
    elif action == "submit_final_additional_info":
        note = _first_text(
            payload.get("final_additional_info"),
            payload.get("additional_info"),
            payload.get("answer"),
            payload.get("note"),
        )
        if note and _normalized_placeholder(note) not in PLACEHOLDER_VALUES:
            intake_state["final_additional_info"] = note
        intake_state["final_plan_confirmed"] = True
    elif action == "submit_risk_factors":
        intake_state["risk_factors"] = _birth_journey_text_or_skipped(payload, "risk_factors")
    elif action == "submit_personalized_followup":
        topic = _first_text(
            payload.get("topic_id"),
            payload.get("topic"),
            payload.get("followup_id"),
            payload.get("id"),
            payload.get("current_followup_id"),
        )
        answer = _birth_journey_text_or_skipped(payload, "answer")
        question = _first_text(payload.get("question"), payload.get("followup_question"))
        plan_impact = _first_text(payload.get("plan_impact"), payload.get("impact"), payload.get("summary"))
        if not topic:
            topic = f"model_followup_{len(_birth_journey_personalized_followup_records(intake_state)) + 1}"
        candidates_by_id = _birth_journey_personalized_followup_candidates_by_id(intake_state)
        if not plan_impact:
            plan_impact = _first_text(candidates_by_id.get(topic, {}).get("meaning"))
        followups = _dict_value(intake_state.get("personalized_followups"))
        followups[topic] = answer
        intake_state["personalized_followups"] = followups
        records = _birth_journey_personalized_followup_records(intake_state)
        record: dict[str, str] = {"topic": topic, "answer": answer}
        if question:
            record["question"] = question
        if plan_impact:
            record["plan_impact"] = plan_impact
        records.append(record)
        intake_state["personalized_followup_records"] = records[:BIRTH_JOURNEY_MODEL_FOLLOWUP_MAX_ROUNDS]
        intake_state.pop("last_personalized_followup_mismatch", None)
        intake_state.pop("active_personalized_followup_id", None)
        if any(
            _birth_journey_model_followup_done_value(payload.get(key))
            for key in ("complete_personalization", "done", "finished", "ready_for_checkup_records")
        ):
            intake_state["personalized_followup_done"] = True
    elif action == "finish_personalized_followups":
        note = _first_text(payload.get("summary"), payload.get("answer"), payload.get("note"))
        if note:
            intake_state["personalized_followup_summary"] = note
        intake_state["personalized_followup_done"] = True
        intake_state.pop("active_personalized_followup_id", None)
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
    initial_analysis = _birth_journey_initial_analysis(plan_context)
    checkup_report_strategy = _birth_journey_checkup_report_strategy(intake_state)
    personalization_tags = _birth_journey_personalization_tags(intake_state)
    personalization_context = _birth_journey_personalization_context(intake_state)
    intake_state.pop("active_personalized_followup_id", None)
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
            "confirmation_question": _birth_journey_intake_question(next_step, plan_context, intake_state),
            "completed_groups": intake_state["completed_groups"],
            "initial_analysis": initial_analysis,
            "checkup_report_strategy": checkup_report_strategy,
            "personalization_tags": personalization_tags,
        },
    }
    if next_step == "personalized_followup":
        result["data"]["personalization_context"] = personalization_context
    if next_step == "basic_info_form":
        result["form"] = _birth_journey_basic_info_form(plan_context, inputs)
    if next_step == "checkup_records_upload":
        result["data"]["upload_panel"] = {
            "title": "上传产检记录",
            "description": "如果产检报告在手边，可以上传最近一次或目前能找到的产检记录；上传完后告诉我“产检记录上传完毕”。如果报告不在手边，也可以先跳过。",
            "done_text": "产检记录上传完毕",
            "skip_text": "先跳过这步",
        }
    if next_step == "generate_plan":
        result["plan_context"] = plan_context
    return result


def _existing_birth_journey_care_plan(inputs: RuntimeInputs, *, require_reusable: bool = True) -> dict[str, Any] | None:
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
            if not require_reusable:
                return plan
            payload = plan.get("payload")
            if isinstance(payload, dict) and payload and _birth_journey_existing_plan_is_reusable(payload):
                return plan
    return None


def _birth_journey_existing_plan_is_reusable(payload: dict[str, Any]) -> bool:
    if payload.get("todo_engine_version") != BIRTH_JOURNEY_TODO_ENGINE_VERSION:
        return False
    rendered = json.dumps(payload, ensure_ascii=False)
    if any(prefix in rendered for prefix in BIRTH_JOURNEY_PRIORITY_TITLE_PREFIXES.values()):
        return False
    if "目的是" in rendered:
        return False
    if any(token in rendered for token in ("用户希望制定孕期计划", "用户想制定孕期计划", "饮食和运动建议")):
        return False
    if any(title in rendered for title in BIRTH_JOURNEY_STALE_VISIBLE_TITLES):
        return False
    return True


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
                "confirmation_question": "我不太确定你说的是哪一项，可以告诉我当前待办里的编号吗？",
            },
        }
    if not matched_ids:
        return {
            "status": "todo_not_found",
            "summary": "没有匹配到要更新的孕期计划待办。",
            "side_effect_performed": False,
            "plan_type": "birth_journey",
            "plan_id": pid,
            "missing_refs": missing_refs,
            "todo_items": _compact_birth_journey_todo_items(todo_items),
            "data": {
                "confirmation_question": "我没有找到对应事项，可以告诉我当前待办里的编号或完整事项名吗？",
            },
        }

    completed_at = _birth_journey_todo_completed_at() if completed else None
    normalized_source = str(source or "app").strip() or "app"
    _update_birth_journey_todo_plan_completion(
        payload,
        matched_view_ids=matched_ids,
        completed=completed,
        completed_at=completed_at,
        completed_source=normalized_source,
    )
    updated_items = [
        item
        for item in _birth_journey_next_7_todo_items_from_payload(payload)
        if str(item.get("id") or "").strip() in matched_ids
    ]

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
        "summary": "已更新当前孕期计划待办完成状态。",
        "side_effect_performed": True,
        "plan_type": "birth_journey",
        "plan_id": pid,
        "completed": bool(completed),
        "updated_items": _compact_birth_journey_todo_items(updated_items),
        "todo_items": _compact_birth_journey_todo_items(_birth_journey_next_7_todo_items_from_payload(saved_plan.get("payload") or {})),
        "completion_followups": _birth_journey_completion_followups(updated_items) if completed else [],
        "plan": saved_plan,
    }


BIRTH_JOURNEY_CURRENT_TODO_PREFIX = "todo_"


def normalize_birth_journey_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload) if isinstance(payload, dict) else {}
    normalized["todo_engine_version"] = BIRTH_JOURNEY_TODO_ENGINE_VERSION
    normalized = _normalize_birth_journey_todo_plan_payload(normalized)
    normalized = _normalize_birth_journey_generation_context_payload(normalized)
    normalized.pop("planning_layers", None)
    return normalized


def normalize_birth_journey_care_plan_artifact(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    if plan.get("plan_type") != "birth_journey":
        return plan
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    return {
        **plan,
        "payload": normalize_birth_journey_plan_payload(payload),
    }


def _normalize_birth_journey_todo_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    todo_plan = payload.get("todo_plan")
    if not isinstance(todo_plan, dict):
        return payload
    normalized_plan = dict(todo_plan)
    periods = normalized_plan.get("periods") if isinstance(normalized_plan.get("periods"), list) else []
    normalized_periods: list[dict[str, Any]] = []
    for period in periods:
        if not isinstance(period, dict):
            continue
        normalized_period = dict(period)
        normalized_period["items"] = _normalize_birth_journey_todo_plan_items(normalized_period.get("items"))
        normalized_periods.append(normalized_period)
    normalized_plan["periods"] = normalized_periods
    payload["todo_plan"] = normalized_plan
    return payload


def _normalize_birth_journey_todo_plan_items(value: Any) -> list[dict[str, Any]]:
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
            for key in ("reason", "steps", "after_done_value", "completion_followup"):
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
        if not str(item.get("plan_reason") or "").strip():
            item["plan_reason"] = _birth_journey_reason_without_priority_prefix(
                item.get("why_for_you") or item.get("reason") or base_title
            )
        if _birth_journey_reason_hidden(item):
            item["reason"] = ""
            item["why_for_you"] = ""
            item["hide_reason"] = True
        elif not str(item.get("reason") or "").strip():
            item["reason"] = item.get("why_for_you") or base_title
        if not isinstance(item.get("steps"), list) or not item.get("steps"):
            item["steps"] = _birth_journey_plan_item_steps(base_title, based_on)
        if not str(item.get("after_done_value") or "").strip():
            item["after_done_value"] = _birth_journey_after_done_value(base_title, based_on)
        if not str(item.get("completion_followup") or "").strip():
            item["completion_followup"] = _birth_journey_completion_followup(base_title, str(item.get("after_done_value") or ""))
        item["id"] = str(item.get("id") or "").strip() or f"todo_{index + 1:02d}"
        completed = _birth_journey_completed_bool(item.get("completed"))
        item["completed"] = completed
        item["completed_at"] = (str(item.get("completed_at") or "").strip() or None) if completed else None
        item["completed_source"] = (str(item.get("completed_source") or "").strip() or None) if completed else None
        normalized_items.append(_strip_birth_journey_done_criteria(item))
    return normalized_items


def _normalize_birth_journey_generation_context_payload(payload: dict[str, Any]) -> dict[str, Any]:
    generation_context = payload.get("generation_context")
    if not isinstance(generation_context, dict):
        return payload
    normalized_context = dict(generation_context)
    periods = normalized_context.get("periods") if isinstance(normalized_context.get("periods"), list) else []
    normalized_periods: list[dict[str, Any]] = []
    for period in periods:
        if not isinstance(period, dict):
            continue
        normalized_period = dict(period)
        normalized_period["items"] = _strip_birth_journey_done_criteria_from_items(normalized_period.get("items"))
        normalized_periods.append(normalized_period)
    normalized_context["periods"] = normalized_periods
    payload["generation_context"] = normalized_context
    return payload


def _strip_birth_journey_done_criteria_from_items(value: Any) -> list[dict[str, Any]]:
    return [_strip_birth_journey_done_criteria(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strip_birth_journey_done_criteria(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized.pop("done_criteria", None)
    return normalized


def _birth_journey_next_7_todo_items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalize_birth_journey_plan_payload(payload)
    payload.clear()
    payload.update(normalized)
    first_period_items = _birth_journey_todo_period_model_items(payload.get("todo_plan"), 0)
    if first_period_items:
        return _normalize_birth_journey_next_7_todo_items(_birth_journey_next_7_view_items(first_period_items))
    return []


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
    by_source_id = {
        str(item.get("source_item_id") or "").strip(): str(item.get("id") or "").strip()
        for item in items
        if str(item.get("source_item_id") or "").strip() and str(item.get("id") or "").strip()
    }
    for raw_ref in refs:
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        normalized_id = _birth_journey_todo_ref_to_id(ref)
        if normalized_id in by_id:
            matched_ids.add(normalized_id)
            continue
        source_match_id = by_source_id.get(normalized_id)
        if source_match_id:
            matched_ids.add(source_match_id)
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
        source_id = _normalize_birth_journey_todo_match_text(item.get("source_item_id"))
        if source_id and source_id == needle:
            exact_matches.append(item)
            continue
        if not title:
            continue
        if title == needle:
            exact_matches.append(item)
        elif needle in title or title in needle:
            partial_matches.append(item)
    return exact_matches or partial_matches


def _update_birth_journey_todo_plan_completion(
    payload: dict[str, Any],
    *,
    matched_view_ids: set[str],
    completed: bool,
    completed_at: str | None,
    completed_source: str,
) -> None:
    todo_plan = payload.get("todo_plan") if isinstance(payload.get("todo_plan"), dict) else {}
    periods = todo_plan.get("periods") if isinstance(todo_plan.get("periods"), list) else []
    if not periods:
        return
    first_period = periods[0] if isinstance(periods[0], dict) else {}
    items = first_period.get("items") if isinstance(first_period.get("items"), list) else []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        view_id = _birth_journey_next_7_todo_id(index)
        item_id = str(item.get("id") or "").strip()
        if view_id not in matched_view_ids and item_id not in matched_view_ids:
            continue
        item["completed"] = bool(completed)
        item["completed_at"] = completed_at if completed else None
        item["completed_source"] = completed_source if completed else None


def _normalize_birth_journey_todo_match_text(value: Any) -> str:
    return re.sub(r"[\s，。；、,.!！?？:：\-_]+", "", str(value or "").strip().lower())


def _birth_journey_todo_ref_to_id(ref: Any) -> str:
    text = str(ref or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"(?:next7|todo)_\d{1,2}", text):
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
            for key in ("reason", "steps", "after_done_value", "completion_followup"):
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
        if not str(item.get("after_done_value") or "").strip():
            item["after_done_value"] = _birth_journey_after_done_value(base_title, based_on)
        if not str(item.get("completion_followup") or "").strip():
            item["completion_followup"] = _birth_journey_completion_followup(base_title, str(item.get("after_done_value") or ""))
        item["id"] = _birth_journey_todo_ref_to_id(item.get("id")) or _birth_journey_next_7_todo_id(len(normalized_items))
        completed = _birth_journey_completed_bool(item.get("completed"))
        item["completed"] = completed
        item["completed_at"] = (str(item.get("completed_at") or "").strip() or None) if completed else None
        item["completed_source"] = (str(item.get("completed_source") or "").strip() or None) if completed else None
        normalized_items.append(_strip_birth_journey_done_criteria(item))
    return normalized_items


def _birth_journey_next_7_todo_id(index: int) -> str:
    return f"{BIRTH_JOURNEY_CURRENT_TODO_PREFIX}{max(1, int(index) + 1):02d}"


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
        "required": True,
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
        "id": "first_birth",
        "label": "是否第一胎",
        "type": "select",
        "required": True,
        "help_text": None,
        "placeholder": None,
        "default_value": None,
        "options": ["是", "否", "不确定/暂不说"],
    },
    {
        "id": "prior_birth_history",
        "label": "既往孕产情况",
        "type": "text",
        "required": False,
        "help_text": None,
        "placeholder": "如：早产、流产、妊娠糖尿病/高血压、产后出血等；没有可写无",
        "default_value": None,
        "options": None,
    },
    {
        "id": "birth_path",
        "label": "计划分娩方式",
        "type": "select",
        "required": True,
        "help_text": None,
        "placeholder": None,
        "default_value": None,
        "options": ["顺产", "剖宫产", "还没确定"],
    },
    {
        "id": "city_or_country",
        "label": "所在城市/国家",
        "type": "text",
        "required": False,
        "help_text": None,
        "placeholder": "例如：深圳 / 美国加州",
        "default_value": None,
        "options": None,
    },
    {
        "id": "birth_hospital",
        "label": "建档/生产医院",
        "type": "text",
        "required": False,
        "help_text": None,
        "placeholder": "如果还没建档，可以写“还没确定”",
        "default_value": None,
        "options": None,
    },
    {
        "id": "medical_notes",
        "label": "基础疾病或长期用药",
        "type": "text",
        "required": False,
        "help_text": None,
        "placeholder": "如：高血压、糖尿病、甲状腺、肾病、自免、心脏病、哮喘等；没有可写无",
        "default_value": None,
        "options": None,
    },
    {
        "id": "doctor_notes",
        "label": "医生特殊提醒",
        "type": "text",
        "required": False,
        "help_text": None,
        "placeholder": "如：胎盘、胎儿生长、羊水、宫颈、血压血糖、复查等提示；没有可写无",
        "default_value": None,
        "options": None,
    },
)

BIRTH_JOURNEY_BASIC_INFO_FIELD_IDS = tuple(field["id"] for field in BIRTH_JOURNEY_BASIC_INFO_FIELDS)
BIRTH_JOURNEY_BASIC_INFO_REQUIRED_FIELD_IDS = tuple(
    field["id"] for field in BIRTH_JOURNEY_BASIC_INFO_FIELDS if field.get("required")
)
BIRTH_JOURNEY_BASIC_INFO_REQUIRED_ALIASES = {
    "current_week": ("current_week", "due_date_or_week", "birth_prep_due_date_or_week", "gestational_week"),
    "ivf": ("ivf", "birth_prep_ivf"),
    "fetus_count": ("fetus_count", "baby_count", "birth_prep_fetus_count"),
    "age": ("age", "birth_prep_age"),
    "first_birth": ("first_birth", "birth_prep_first_birth"),
    "birth_path": ("birth_path", "delivery_method"),
}


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
            "first_birth",
            "prior_birth_history",
            "birth_path",
            "city_or_country",
            "birth_hospital",
            "medical_notes",
            "doctor_notes",
            "hospital",
        ),
    },
    {
        "id": "checkup_records",
        "label": "产检记录",
        "question": "请上传目前全部产检记录；上传完后告诉我“产检记录上传完毕”。",
        "keys": ("checkup_records_uploaded", "checkup_status", "checkup_records", "uploaded_checkup_records"),
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
        or (field_id in BIRTH_JOURNEY_BASIC_INFO_REQUIRED_FIELD_IDS and str(source.get(field_id) or "").strip())
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
    for field_id in (
        "age",
        "current_week",
        "due_date_or_week",
        "ivf",
        "fetus_count",
        "multiple_pregnancy_type",
        "first_birth",
        "previous_birth_method",
        "previous_c_section_count",
        "prior_birth_history",
        "birth_path",
        "birth_hospital",
        "feeding_intention",
        "medical_notes",
        "doctor_notes",
    ):
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
    if not _birth_journey_missing_required_basic_info_fields(_birth_journey_intake_basic_context(state)):
        groups.append("basic_info")
    if "entry_concern_followup" in state:
        groups.append("entry_concern")
    if state.get("checkup_records_uploaded") is True or _has_meaningful_value(state.get("checkup_status")):
        groups.append("checkup_records")
    if _dict_value(state.get("personalized_followups")):
        groups.append("personalized_followups")
    for field_id in ("risk_factors", "current_symptoms", "lifestyle_context", "feeding_ibclc_context"):
        if field_id in state and field_id not in groups:
            groups.append(field_id)
    return groups


def _birth_journey_intake_next_step(state: dict[str, Any]) -> str:
    if _birth_journey_missing_required_basic_info_fields(_birth_journey_intake_basic_context(state)):
        return "basic_info_form"
    if _birth_journey_should_continue_model_personalized_followup(state):
        return "personalized_followup"
    if _birth_journey_should_ask_checkup_done(state):
        return "checkup_done_question"
    if not _birth_journey_checkup_step_done(state):
        return "checkup_records_upload"
    if _birth_journey_symptoms_need_pause(str(state.get("current_symptoms") or "")):
        return "pause_for_symptoms"
    if state.get("final_plan_confirmed") is not True:
        return "final_plan_confirmation"
    return "generate_plan"


def _birth_journey_intake_basic_context(state: dict[str, Any]) -> dict[str, Any]:
    return {**_dict_value(state.get("entry_known_values")), **_dict_value(state.get("basic_info"))}


def _birth_journey_missing_required_basic_info_fields(context: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field_id in BIRTH_JOURNEY_BASIC_INFO_REQUIRED_FIELD_IDS:
        aliases = BIRTH_JOURNEY_BASIC_INFO_REQUIRED_ALIASES.get(field_id, (field_id,))
        if not any(_birth_journey_basic_info_field_was_provided(context, alias) for alias in aliases):
            missing.append(field_id)
    return missing


def _birth_journey_basic_info_field_was_provided(context: dict[str, Any], field_id: str) -> bool:
    if field_id not in context:
        return False
    value = context.get(field_id)
    if isinstance(value, (list, tuple, set)):
        return any(str(item or "").strip() for item in value)
    return bool(str(value or "").strip())


def _birth_journey_intake_week(state: dict[str, Any]) -> int | None:
    return _birth_journey_context_week(_birth_journey_intake_basic_context(state))


def _birth_journey_context_week(context: dict[str, Any]) -> int | None:
    text = _first_text(
        context.get("current_week"),
        context.get("due_date_or_week"),
        context.get("birth_prep_due_date_or_week"),
        context.get("gestational_week"),
    )
    if not text:
        return None
    match = re.search(r"(\d{1,2})(?:\s*\+\s*\d{1,2})?\s*周", text)
    if not match:
        match = re.search(r"^(\d{1,2})(?:\s*\+\s*\d{1,2})?$", text)
    if not match:
        return None
    week = int(match.group(1))
    return week if 1 <= week <= 45 else None


def _birth_journey_stage_from_week(week: int | None) -> str:
    if week is None:
        return "unknown"
    if week <= 13:
        return "early"
    if week <= 27:
        return "mid"
    return "late"


def _birth_journey_checkup_step_done(state: dict[str, Any]) -> bool:
    return state.get("checkup_records_uploaded") is True or "checkup_status" in state


def _birth_journey_should_ask_checkup_done(state: dict[str, Any]) -> bool:
    if _birth_journey_checkup_step_done(state) or "checkup_done_confirmed" in state:
        return False
    return _birth_journey_stage_from_week(_birth_journey_intake_week(state)) == "early"


BIRTH_JOURNEY_RISK_RELEVANT_PERSONALIZATION_TAGS = {
    "age_35_plus",
    "age_40_plus",
    "ivf",
    "multiple_pregnancy",
    "prior_c_section",
    "prior_preterm",
    "hypertension_or_preeclampsia",
    "diabetes_or_gdm",
    "chronic_medical_condition",
    "doctor_special_notes",
    "prior_adverse_pregnancy",
}
BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_LIMIT = 5
BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_TARGET_WITH_SIGNAL = 1
BIRTH_JOURNEY_MODEL_FOLLOWUP_MAX_ROUNDS = 3
BIRTH_JOURNEY_PERSONALIZED_REPLY_GUIDANCE = (
    "不清楚也可以回“还不确定”，我会先放进下次产检待确认。"
)
BIRTH_JOURNEY_PERSONALIZED_DEFAULT_OPTIONS = ("还不确定", "先放进待确认", "我补充一下")


def _birth_journey_context_text(context: dict[str, Any], *keys: str) -> str:
    values: list[str] = []
    for key in keys:
        text = _birth_journey_substantive_text(context.get(key))
        if text:
            values.append(text)
    return "；".join(_unique_text_list(values, 8))


def _birth_journey_text_has_any(value: Any, tokens: tuple[str, ...]) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and any(token.lower() in text for token in tokens)


def _birth_journey_positive_number(value: Any) -> int | None:
    try:
        number = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _birth_journey_has_prior_c_section(context: dict[str, Any]) -> bool:
    method = _birth_journey_substantive_text(context.get("previous_birth_method"))
    count = _birth_journey_positive_number(context.get("previous_c_section_count"))
    history = _birth_journey_context_text(context, "prior_birth_history", "pregnancy_history_or_notes")
    if count:
        return True
    return _birth_journey_text_has_any(f"{method}；{history}", ("剖", "c-section", "cesarean", "cesarian"))


def _birth_journey_prior_history_text(context: dict[str, Any]) -> str:
    return _birth_journey_context_text(context, "prior_birth_history", "pregnancy_history_or_notes")


def _birth_journey_medical_condition_text(context: dict[str, Any]) -> str:
    return _birth_journey_context_text(context, "medical_notes", "chronic_conditions", "long_term_medication")


def _birth_journey_doctor_note_text(context: dict[str, Any]) -> str:
    return _birth_journey_context_text(context, "doctor_notes", "special_notes", "abnormal_results")


def _birth_journey_risk_signal_text(context: dict[str, Any]) -> str:
    return "；".join(
        _unique_text_list(
            [
                _birth_journey_prior_history_text(context),
                _birth_journey_medical_condition_text(context),
                _birth_journey_doctor_note_text(context),
                _birth_journey_substantive_text(context.get("risk_factors")),
            ],
            8,
        )
    )


def _birth_journey_has_prior_preterm(context: dict[str, Any]) -> bool:
    return _birth_journey_text_has_any(_birth_journey_prior_history_text(context), ("早产", "preterm", "premature"))


def _birth_journey_has_hypertension_context(context: dict[str, Any]) -> bool:
    return _birth_journey_text_has_any(
        _birth_journey_risk_signal_text(context),
        ("高血压", "血压", "子痫", "preeclampsia", "hypertension"),
    )


def _birth_journey_has_diabetes_context(context: dict[str, Any]) -> bool:
    return _birth_journey_text_has_any(
        _birth_journey_risk_signal_text(context),
        ("糖尿病", "妊娠糖尿病", "血糖", "糖耐", "gdm", "diabetes"),
    )


def _birth_journey_has_chronic_medical_context(context: dict[str, Any]) -> bool:
    return _birth_journey_text_has_any(
        _birth_journey_medical_condition_text(context),
        (
            "甲状腺",
            "肾",
            "心脏",
            "自身免疫",
            "免疫",
            "红斑狼疮",
            "抗磷脂",
            "哮喘",
            "癫痫",
            "血栓",
            "凝血",
            "thyroid",
            "kidney",
            "renal",
            "heart",
            "autoimmune",
            "lupus",
            "asthma",
            "epilepsy",
        ),
    )


def _birth_journey_has_adverse_pregnancy_history(context: dict[str, Any]) -> bool:
    return _birth_journey_text_has_any(
        _birth_journey_prior_history_text(context),
        ("流产", "胎停", "死胎", "生长受限", "fgr", "产后出血", "肩难产", "早剥"),
    )


def _birth_journey_personalized_followup_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    context = _birth_journey_intake_basic_context(state)
    age = _birth_journey_context_age(context)
    ivf = _first_text(context.get("ivf"), context.get("birth_prep_ivf"))
    fetus_count = _first_text(context.get("fetus_count"), context.get("birth_prep_fetus_count"))
    multiple_type = _first_text(context.get("multiple_pregnancy_type"), context.get("birth_prep_multiple_pregnancy_type"))
    first_birth = _normalize_first_birth(_first_text(context.get("first_birth"), context.get("birth_prep_first_birth")))
    previous_birth_method = _birth_journey_substantive_text(context.get("previous_birth_method"))
    birth_path = _normalize_birth_journey_birth_path(_first_text(context.get("birth_path"), context.get("delivery_method")))
    prior_history_text = _birth_journey_prior_history_text(context)
    medical_text = _birth_journey_medical_condition_text(context)
    doctor_note_text = _birth_journey_doctor_note_text(context)
    is_multiple_pregnancy = _birth_journey_is_multiple_pregnancy(fetus_count)
    age_multiple_followup: dict[str, Any] | None = None
    if age is not None and age >= 35 and is_multiple_pregnancy:
        fetus_label = fetus_count if _birth_journey_substantive_text(fetus_count) else "双胎/多胎"
        age_multiple_followup = {
            "id": "age_35_plus_multiple_monitoring",
            "tag": "age_35_plus",
            "tags": ("age_35_plus", "multiple_pregnancy"),
            "title": "高龄和多胎监测信息",
            "observation": f"我注意到你{age}岁，产科管理上会归入“高龄孕产妇”范围；同时这次是{fetus_label}，这两点都需要认真放进计划里。",
            "meaning": "高龄和多胎都会让产检更关注筛查选择、血压血糖、用药复查、胎儿生长、宫颈长度和早产信号。",
            "key_points": (
                f"{age}岁不是单纯年龄数字，产科管理上会按高龄孕产妇更早关注筛查选择、血压血糖和胎儿生长。",
                f"{fetus_label}会让计划更关注两个宝宝的生长差异、宫颈长度、复查频率和早产信号。",
            ),
            "followup_question": "你现在有没有已经被提醒过或正在复查/用药的情况？比如血压/血糖、甲状腺/免疫或长期用药、胎儿生长、宫颈长度或胎盘羊水；如果都没有，可以直接说暂无异常。",
            "reply_guidance": "不确定也可以回“还不确定”，我会把需要确认的监测项放进下次产检待确认。",
            "reply_options": ("有复查/用药", "胎儿/宫颈监测", "暂无异常"),
        }
    candidates: list[dict[str, Any]] = []
    if doctor_note_text:
        candidates.append(
            {
                "id": "doctor_special_notes_followup",
                "tag": "doctor_special_notes",
                "title": "医生特殊提醒",
                "observation": "我看到你填了医生或产检里的特殊提醒。",
                "meaning": "这类信息最适合先拆成复查时间、日常观察和异常联系规则。",
                "key_points": ("医生或产检里的特殊提醒，需要转成复查时间、日常观察指标和异常联系路径。",),
                "followup_question": "这个提醒主要和胎盘/羊水、宫颈/胎儿生长，还是血压血糖/其他情况有关？",
                "reply_guidance": "如果一时分不清，可以回“还不确定”，我会先按医生提醒待确认放进计划。",
                "reply_options": ("胎盘/羊水", "宫颈/胎儿生长", "血压血糖/其他"),
            }
        )
    has_prior_c_section = _birth_journey_has_prior_c_section(context)
    generic_prior_birth_needed = first_birth == "否" and not previous_birth_method and not prior_history_text
    if has_prior_c_section:
        candidates.append(
            {
                "id": "prior_c_section_birth_path_detail",
                "tag": "prior_c_section",
                "title": "既往剖宫产信息",
                "observation": "我注意到你之前有过剖宫产。",
                "meaning": "这会影响这次分娩方式评估，也会影响后面是否需要提前准备上次手术信息。",
                "key_points": ("既往剖宫产会影响这次分娩方式评估、胎盘位置关注和上次手术资料准备。",),
                "followup_question": "你还记得上次剖宫产的主要原因吗，比如胎位、产程原因，还是记不清了？",
                "reply_guidance": "记不清也没关系，可以回“记不清”，我会先把上次手术原因列为待确认。",
                "reply_options": ("胎位/臀位", "产程原因", "记不清"),
            }
        )
    elif generic_prior_birth_needed:
        if age_multiple_followup:
            candidates.append(age_multiple_followup)
        candidates.append(
            {
                "id": "prior_birth_history_detail",
                "tag": "prior_birth_history",
                "title": "既往生产经历",
                "observation": "我看到这次不是第一胎。",
                "meaning": "之前的生产方式和恢复经历，会影响这次分娩沟通和产后准备。",
                "key_points": ("不是第一胎时，上一胎的生产方式、早产或产后出血经历会影响这次分娩沟通和产后准备。",),
                "followup_question": "上一胎主要是顺产、剖宫产，还是有早产/产后出血/严重撕裂这类经历？",
                "reply_guidance": "如果记不清细节，可以先回一个大概类型，我会把细节放进待确认。",
                "reply_options": ("顺产", "剖宫产", "有特殊经历/记不清"),
            }
        )
    if _birth_journey_has_prior_preterm(context):
        candidates.append(
            {
                "id": "prior_preterm_monitoring_detail",
                "tag": "prior_preterm",
                "title": "既往早产史",
                "observation": "我看到你提到过早产相关经历。",
                "meaning": "这次计划会更关注宫颈情况、复查频率和早产信号。",
                "key_points": ("既往早产会让这次计划更早关注宫颈长度、复查频率和早产信号。",),
                "followup_question": "你还记得上次早产大概发生在多少周吗，是 34 周前、34 周后，还是记不清？",
                "reply_guidance": "记不清也可以直接说，我会先把早产周数放进下次产检待确认。",
                "reply_options": ("34周前", "34周后", "记不清"),
            }
        )
    if _birth_journey_has_hypertension_context(context):
        candidates.append(
            {
                "id": "hypertension_or_preeclampsia_monitoring",
                "tag": "hypertension_or_preeclampsia",
                "title": "血压和子痫前期风险管理",
                "observation": "我看到你填到血压或子痫前期相关信息。",
                "meaning": "这会影响在家观察、复查指标和异常时联系医院的规则。",
                "key_points": ("血压或子痫前期相关信息，会影响家庭血压记录、尿蛋白等复查指标和异常联系规则。",),
                "followup_question": "你现在手里有需要记录的血压范围、复查时间，还是还没有明确口径？",
                "reply_guidance": "没有明确口径也可以回“还不确定”，我会把血压记录规则放进待确认。",
                "reply_options": ("有血压范围", "有复查时间", "还不确定"),
            }
        )
    if _birth_journey_has_diabetes_context(context):
        candidates.append(
            {
                "id": "diabetes_or_gdm_monitoring",
                "tag": "diabetes_or_gdm",
                "title": "血糖和糖耐相关管理",
                "observation": "我看到你填到血糖、糖尿病或糖耐相关信息。",
                "meaning": "这会影响饮食记录、血糖复查和胎儿生长监测的安排。",
                "key_points": ("血糖、糖尿病或糖耐相关信息，会影响饮食记录、血糖复查节奏和胎儿生长监测。",),
                "followup_question": "你现在是已经做过糖耐/血糖复查，还是还没到检查时间？",
                "reply_guidance": "如果不确定检查进度，可以回“还不确定”，我会先按待确认推进。",
                "reply_options": ("已做过", "还没到时间", "还不确定"),
            }
        )
    if _birth_journey_has_chronic_medical_context(context):
        candidates.append(
            {
                "id": "chronic_medical_condition_coordination",
                "tag": "chronic_medical_condition",
                "title": "基础疾病和用药确认",
                "observation": "我看到你填了基础疾病或长期用药。",
                "meaning": "这类信息会影响用药确认、专科复查和产科复查频率。",
                "key_points": ("基础疾病或长期用药会影响用药安全确认、专科复查和产科复查频率。",),
                "followup_question": "你现在有没有正在长期吃药、固定专科复查，或产科要求更密集复查的情况？",
                "reply_guidance": "如果分不清，可以回“还不确定”，我会先放进产科和专科共同确认。",
                "reply_options": ("长期用药", "专科复查", "还不确定"),
            }
        )
    if _birth_journey_has_adverse_pregnancy_history(context):
        candidates.append(
            {
                "id": "prior_adverse_pregnancy_detail",
                "tag": "prior_adverse_pregnancy",
                "title": "既往孕产异常经历",
                "observation": "我看到你填到既往孕产异常经历。",
                "meaning": "这次计划会更关注复查节点、胎儿生长和异常联系路径。",
                "key_points": ("既往孕产异常经历会影响这次复查节点、胎儿生长观察和异常联系路径。",),
                "followup_question": "这段经历更接近流产/胎停、胎儿生长受限，还是产后出血/其他情况？",
                "reply_guidance": "暂时说不清也可以回“还不确定”，我会先作为产检沟通清单保留。",
                "reply_options": ("流产/胎停", "胎儿生长受限", "其他/不确定"),
            }
        )
    if age_multiple_followup and not generic_prior_birth_needed:
        candidates.append(age_multiple_followup)
    if age is not None and age >= 35 and not is_multiple_pregnancy:
        candidates.append(
            {
                "id": "age_35_plus_checkup_detail",
                "tag": "age_35_plus",
                "title": "高龄孕产妇管理信息",
                "observation": f"我注意到你{age}岁，按产科管理属于“高龄孕产妇”，这个信息需要认真放进计划里。",
                "meaning": "高龄会让筛查选择、血压血糖、甲状腺或用药复查、胎儿生长和胎盘情况更需要提前对齐。",
                "key_points": (
                    f"{age}岁在产科管理上会归入高龄孕产妇范围，计划里要更早对齐筛查选择和复查节奏。",
                    "高龄相关计划重点通常包括血压血糖、甲状腺或用药复查、胎儿生长和胎盘情况。",
                ),
                "followup_question": "你现在有没有已经被提醒过或正在复查的情况？比如血压/血糖、甲状腺/免疫或长期用药、胎儿生长或胎盘羊水；如果都没有，也可以直接说暂无异常。",
                "reply_guidance": "不确定也可以回“还不确定”，我会把这些放进下次产检待确认。",
                "reply_options": ("血压/血糖", "甲状腺/用药", "暂无异常"),
            }
        )
    if _birth_journey_text_is_yes(ivf):
        candidates.append(
            {
                "id": "ivf_week_confirmation",
                "tag": "ivf",
                "title": "IVF 孕周和复查口径",
                "observation": "我注意到你是 IVF/辅助生殖怀孕。",
                "meaning": "这会影响孕周口径，也可能涉及黄体支持、甲状腺或凝血/免疫用药复查。",
                "key_points": ("IVF/辅助生殖会影响孕周和预产期口径，也可能涉及黄体支持、甲状腺或凝血/免疫用药复查。",),
                "followup_question": "IVF 这边需要纳入计划的是哪类信息：移植日期/孕周口径、黄体支持或其他用药，还是目前没有特殊复查？",
                "reply_guidance": "如果暂时说不清，可以回“按待确认”，我会把孕周口径和用药复查都列进下次产检确认。",
                "reply_options": ("孕周口径", "用药/复查", "目前没有"),
            }
        )
    if is_multiple_pregnancy and not (age is not None and age >= 35):
        if _birth_journey_substantive_text(multiple_type) and "不适用" not in str(multiple_type):
            type_suffix = f"你填的是{multiple_type}。"
        elif _birth_journey_substantive_text(fetus_count):
            type_suffix = f"我注意到你填的是{fetus_count}。"
        else:
            type_suffix = "我注意到你填的是多胎。"
        candidates.append(
            {
                "id": "multiple_pregnancy_monitoring",
                "tag": "multiple_pregnancy",
                "title": "多胎监测重点",
                "observation": type_suffix,
                "meaning": "这会让产检更关注胎儿生长、宫颈长度和早产信号。",
                "key_points": ("双胎/多胎会让计划更关注胎儿生长差异、宫颈长度、复查频率和早产信号。",),
                "followup_question": "目前产检记录里的双胎类型是哪一类：单绒双羊、双绒双羊，还是暂时没确认？",
                "reply_guidance": "暂时没确认也没关系，我会先把双胎类型放进下次产检待确认。",
                "reply_options": ("单绒双羊", "双绒双羊", "还没确认"),
            }
        )
    if "剖" in birth_path:
        candidates.append(
            {
                "id": "planned_c_section_detail",
                "tag": "planned_c_section",
                "title": "剖宫产沟通重点",
                "observation": "我看到你计划剖宫产。",
                "meaning": "剖宫产会影响术前检查、禁食入院、住院天数和伤口护理准备。",
                "key_points": ("计划剖宫产会影响术前检查、禁食入院、住院天数和伤口护理准备。",),
                "followup_question": "这次计划剖宫产主要是因为既往剖宫产、胎盘/胎位/双胎等医学原因，还是目前只是个人倾向或待定？",
                "reply_guidance": "如果原因还没确定，可以回“待定”，我会先把剖宫产原因和时间都列为产检待确认。",
                "reply_options": ("既往剖宫产", "胎盘/胎位/双胎", "待定"),
            }
        )
    return candidates[:BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_LIMIT]


def _birth_journey_followup_source_prefix(state: dict[str, Any]) -> str:
    context = _birth_journey_intake_basic_context(state)
    parts: list[str] = []
    age = _birth_journey_context_age(context)
    if age is not None and age >= 35:
        parts.append(f"你{age}岁，属于高龄孕产妇管理范围")
    if _birth_journey_text_is_yes(_first_text(context.get("ivf"), context.get("birth_prep_ivf"))):
        parts.append("你是 IVF/辅助生殖怀孕")
    fetus_count = _first_text(context.get("fetus_count"), context.get("birth_prep_fetus_count"))
    if _birth_journey_is_multiple_pregnancy(fetus_count):
        parts.append(f"你填的是{fetus_count}")
    first_birth = _normalize_first_birth(_first_text(context.get("first_birth"), context.get("birth_prep_first_birth")))
    if first_birth == "否":
        parts.append("这次不是第一胎")
    week_text = _first_text(context.get("current_week"), context.get("due_date_or_week"))
    if week_text:
        parts.append(f"你现在是{week_text}")
    if not parts:
        return ""
    return "结合" + "、".join(_unique_text_list(parts, 3)) + "，"


def _birth_journey_plan_critical_followup_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    week = _birth_journey_intake_week(state)
    stage = _birth_journey_stage_from_week(week)
    source_prefix = _birth_journey_followup_source_prefix(state)
    if stage == "early":
        milestone_question = "现在早孕关键节点里，宫内孕/胎心、NT或早筛这些项目属于已确认、已预约，还是还没到检查时间？"
        milestone_options = ("已确认/已预约", "还没到时间", "还不确定")
        milestone_meaning = "早孕计划最容易漏的是检查窗口，先把已确认和待预约的节点分清楚。"
    elif stage == "late":
        milestone_question = "最近一次产检里，胎动、胎位、胎盘羊水、胎心监护或入院信号这些项目是正常、需要复查，还是还没确认？"
        milestone_options = ("目前正常", "需要复查", "还没确认")
        milestone_meaning = "孕晚期计划会围绕胎动观察、复查节奏和入院准备来排。"
    else:
        milestone_question = "中期筛查和大排畸节点里，唐筛/无创/羊穿、大排畸或糖耐现在属于已完成、已预约，还是还没安排？"
        milestone_options = ("已完成", "已预约", "还没安排")
        milestone_meaning = "孕中期计划需要先对齐筛查、大排畸和糖耐这些时间窗口。"

    return [
        {
            "id": "pregnancy_milestone_status_detail",
            "tag": "pregnancy_milestone_status",
            "title": "阶段产检节点",
            "observation": source_prefix + "我再把这一阶段最容易影响计划节奏的产检节点确认一下。",
            "meaning": milestone_meaning,
            "followup_question": milestone_question,
            "reply_guidance": "不清楚也可以回“还不确定”，我会先按待确认放进计划。",
            "reply_options": milestone_options,
        },
        {
            "id": "care_site_status_detail",
            "tag": "care_site_status",
            "title": "建档和生产医院路径",
            "observation": source_prefix + "我还需要把建档、产检和生产医院这条执行路径放进计划里。",
            "meaning": "不同城市和医院的建档材料、复查节奏和入院流程会不太一样。",
            "followup_question": "现在产检和计划生产是在同一家医院吗，还是生产医院还没确定/后面可能转院？",
            "reply_guidance": "如果还没确定，可以直接回“还没确定”，计划里会先留出确认项。",
            "reply_options": ("同一家医院", "还没确定", "可能转院"),
        },
        {
            "id": "gestational_week_basis_detail",
            "tag": "gestational_week_basis",
            "title": "孕周口径",
            "observation": source_prefix + "我再确认一下孕周口径，避免后面的检查窗口排偏。",
            "meaning": "孕周口径会影响 NT、筛查、大排畸、糖耐、胎心监护这些节点的时间。",
            "followup_question": "你现在的孕周主要是按末次月经、早孕B超/NT，还是医生已经确认过的预产期来算？",
            "reply_guidance": "如果不确定，可以回“还不确定”，我会把孕周口径放进待确认。",
            "reply_options": ("末次月经", "早孕B超/NT", "医生确认预产期"),
        },
    ]


def _append_unique_birth_journey_followup(queue: list[dict[str, Any]], followup: dict[str, Any]) -> None:
    followup_id = str(followup.get("id") or "").strip()
    if not followup_id or any(str(item.get("id") or "").strip() == followup_id for item in queue):
        return
    queue.append(followup)


def _build_birth_journey_personalized_followup_queue(state: dict[str, Any]) -> list[dict[str, Any]]:
    primary_followups = _birth_journey_personalized_followup_candidates(state)
    if not primary_followups:
        return []

    queue: list[dict[str, Any]] = []
    for followup in primary_followups:
        _append_unique_birth_journey_followup(queue, followup)
        if len(queue) >= BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_LIMIT:
            return queue

    target_count = min(
        BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_TARGET_WITH_SIGNAL,
        BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_LIMIT,
    )
    if len(queue) < target_count:
        for followup in _birth_journey_plan_critical_followup_candidates(state):
            _append_unique_birth_journey_followup(queue, followup)
            if len(queue) >= target_count:
                break
    return queue[:BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_LIMIT]


def _birth_journey_saved_personalized_followup_queue(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw_queue = state.get("personalized_followup_queue")
    if not isinstance(raw_queue, list):
        return []
    queue: list[dict[str, Any]] = []
    for item in raw_queue:
        if not isinstance(item, dict):
            continue
        followup_id = str(item.get("id") or "").strip()
        if not followup_id:
            continue
        _append_unique_birth_journey_followup(queue, item)
        if len(queue) >= BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_LIMIT:
            break
    return queue


def _birth_journey_personalized_followup_queue(state: dict[str, Any]) -> list[dict[str, Any]]:
    saved_queue = _birth_journey_saved_personalized_followup_queue(state)
    if saved_queue:
        return saved_queue
    return _build_birth_journey_personalized_followup_queue(state)


def _refresh_birth_journey_personalized_followup_queue(state: dict[str, Any]) -> None:
    if _dict_value(state.get("personalized_followups")):
        return
    queue = _build_birth_journey_personalized_followup_queue(state)
    if queue:
        state["personalized_followup_queue"] = queue
    else:
        state.pop("personalized_followup_queue", None)


def _birth_journey_personalized_reply_option_texts(followup: dict[str, Any]) -> list[str]:
    raw_options = followup.get("reply_options")
    if isinstance(raw_options, str):
        candidates = re.split(r"[|｜、,，/]+", raw_options)
    elif isinstance(raw_options, (list, tuple)):
        candidates = [str(item or "") for item in raw_options]
    else:
        candidates = []
    options: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        options.append(text)
        if len(options) >= 3:
            break
    return options


def _birth_journey_personalized_key_points(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, (list, tuple)) else str(value).splitlines()
    key_points: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        text = str(raw_item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        key_points.append(text)
        if len(key_points) >= 3:
            break
    return key_points


def _birth_journey_personalized_followup_payload(followup: dict[str, Any]) -> dict[str, Any]:
    payload = dict(followup)
    observation = str(payload.get("observation") or "").strip()
    meaning = str(payload.get("meaning") or "").strip()
    followup_question = str(payload.get("followup_question") or payload.get("question") or "").strip()
    payload.setdefault("supportive_prefix", observation)
    payload.setdefault("explanation", " ".join(part for part in (observation, meaning) if part))
    payload["question"] = followup_question
    payload.setdefault("followup_question", followup_question)
    payload.setdefault("reply_guidance", BIRTH_JOURNEY_PERSONALIZED_REPLY_GUIDANCE)
    if not _birth_journey_personalized_reply_option_texts(payload):
        payload["reply_options"] = BIRTH_JOURNEY_PERSONALIZED_DEFAULT_OPTIONS
    key_points = _birth_journey_personalized_key_points(payload.get("key_points"))
    if key_points:
        payload["key_points"] = key_points
        payload.setdefault(
            "must_mention",
            "追问前按“用户信息 -> 孕期管理意义 -> 计划影响 -> 一个具体追问”表达；"
            "必须点出 key_points 里的关键信息为什么会影响计划，不要只复述用户填写的字段。",
        )
    return payload


def _birth_journey_personalized_followup_prompt_text(followup: dict[str, Any] | None) -> str:
    if not isinstance(followup, dict):
        return ""
    observation = str(followup.get("observation") or "").strip()
    meaning = str(followup.get("meaning") or "").strip()
    question = str(followup.get("followup_question") or followup.get("question") or "").strip()
    reply_guidance = str(followup.get("reply_guidance") or "").strip()
    return "\n\n".join(part for part in (observation, meaning, question, reply_guidance) if part)


def _birth_journey_next_personalized_followup(state: dict[str, Any]) -> dict[str, Any] | None:
    answered = _dict_value(state.get("personalized_followups"))
    for followup in _birth_journey_personalized_followup_queue(state):
        followup_id = str(followup.get("id") or "").strip()
        if followup_id and followup_id not in answered:
            return _birth_journey_personalized_followup_payload(followup)
    return None


def _birth_journey_model_followup_done_value(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {
            "true",
            "1",
            "yes",
            "done",
            "completed",
            "finish",
            "finished",
            "完成",
            "已完成",
            "结束",
            "够了",
            "不用继续",
        }
    return False


def _birth_journey_personalized_followup_records(state: dict[str, Any]) -> list[dict[str, str]]:
    raw_records = state.get("personalized_followup_records")
    if not isinstance(raw_records, list):
        return []
    records: list[dict[str, str]] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            continue
        topic = _first_text(raw_record.get("topic_id"), raw_record.get("topic"), raw_record.get("followup_id"), raw_record.get("id"))
        answer = _first_answer_text(raw_record.get("answer"), raw_record.get("text"), raw_record.get("note"))
        question = _first_text(raw_record.get("question"), raw_record.get("followup_question"))
        plan_impact = _first_text(raw_record.get("plan_impact"), raw_record.get("impact"), raw_record.get("summary"))
        if not topic and not answer and not question:
            continue
        record: dict[str, str] = {}
        if topic:
            record["topic"] = topic
        if question:
            record["question"] = question
        if answer:
            record["answer"] = answer
        if plan_impact:
            record["plan_impact"] = plan_impact
        records.append(record)
        if len(records) >= BIRTH_JOURNEY_MODEL_FOLLOWUP_MAX_ROUNDS:
            break
    return records


def _birth_journey_model_answered_followup_topics(state: dict[str, Any]) -> set[str]:
    topics = {
        str(topic or "").strip()
        for topic in _dict_value(state.get("personalized_followups")).keys()
        if str(topic or "").strip()
    }
    for record in _birth_journey_personalized_followup_records(state):
        topic = str(record.get("topic") or "").strip()
        if topic:
            topics.add(topic)
    return topics


def _birth_journey_model_suggested_followup_topics(state: dict[str, Any]) -> list[dict[str, Any]]:
    answered_topics = _birth_journey_model_answered_followup_topics(state)
    topics: list[dict[str, Any]] = []
    for followup in _birth_journey_personalized_followup_candidates(state):
        payload = _birth_journey_personalized_followup_payload(followup)
        topic_id = str(payload.get("id") or "").strip()
        if topic_id and topic_id in answered_topics:
            continue
        topics.append(payload)
        if len(topics) >= BIRTH_JOURNEY_PERSONALIZED_FOLLOWUP_LIMIT:
            break
    return topics


def _birth_journey_should_continue_model_personalized_followup(state: dict[str, Any]) -> bool:
    if state.get("personalized_followup_done") is True:
        return False
    if len(_birth_journey_personalized_followup_records(state)) >= BIRTH_JOURNEY_MODEL_FOLLOWUP_MAX_ROUNDS:
        return False
    return bool(_birth_journey_model_suggested_followup_topics(state))


def _birth_journey_model_followup_profile_facts(state: dict[str, Any]) -> dict[str, Any]:
    context = _birth_journey_intake_basic_context(state)
    facts: dict[str, Any] = {}
    for key in (
        "current_week",
        "due_date_or_week",
        "age",
        "ivf",
        "fetus_count",
        "first_birth",
        "birth_path",
        "city_or_country",
        "birth_hospital",
        "prior_birth_history",
        "medical_notes",
        "doctor_notes",
    ):
        value = context.get(key)
        if _has_meaningful_value(value):
            facts[key] = value
    week = _birth_journey_intake_week(state)
    if week:
        facts["stage"] = _birth_journey_stage_from_week(week)
    return facts


def _birth_journey_model_followup_implications(topics: list[dict[str, Any]]) -> list[dict[str, str]]:
    implications: list[dict[str, str]] = []
    for topic in topics:
        topic_id = str(topic.get("id") or "").strip()
        title = str(topic.get("title") or "").strip()
        meaning = str(topic.get("meaning") or "").strip()
        if not topic_id or not meaning:
            continue
        implications.append({"topic": topic_id, "title": title, "plan_impact": meaning})
    return implications


def _birth_journey_personalization_context(state: dict[str, Any]) -> dict[str, Any]:
    suggested_topics = _birth_journey_model_suggested_followup_topics(state)
    asked_followups = _birth_journey_personalized_followup_records(state)
    asked_count = len(asked_followups)
    return {
        "mode": "model_driven_followup",
        "profile_facts": _birth_journey_model_followup_profile_facts(state),
        "planning_implications": _birth_journey_model_followup_implications(suggested_topics),
        "suggested_topics": suggested_topics,
        "asked_followups": asked_followups,
        "followup_policy": {
            "max_rounds": BIRTH_JOURNEY_MODEL_FOLLOWUP_MAX_ROUNDS,
            "asked_count": asked_count,
            "remaining_rounds": max(0, BIRTH_JOURNEY_MODEL_FOLLOWUP_MAX_ROUNDS - asked_count),
            "ask_only_one_question": True,
            "do_not_repeat_topics": True,
            "skip_counts_as_answered": True,
            "may_finish_when_enough": True,
        },
        "response_contract": (
            "遵循 birth-prep skill 的个性化追问规则：用户信息 -> 孕期管理意义 -> 计划影响 -> 一个具体追问。"
            "个人信息必须展开背后的管理意义，尤其年龄>=35时要说出高龄孕产妇/产科管理范围及计划影响。"
        ),
        "decision_instruction": (
            "基于 profile_facts、planning_implications 和 suggested_topics 自主决定本轮是否还需要追问。"
            "需要追问时，只选择一个最会影响计划安排的 topic，用 topic/id 记录；"
            "如果多个相关因素已被合并在同一 topic，可以问一个合并后的明确问题，但不要拆成多题问卷。"
            "追问前要按 response_contract 点出所选 topic 的 key_points 或 meaning 里的计划意义，不能只复述用户填写了什么；"
            "如果用户已经回答没有异常、跳过、或你判断信息已足够，调用 finish_personalized_followups。"
        ),
    }


def _birth_journey_personalization_tags(state: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for item in _birth_journey_personalized_followup_queue(state):
        raw_tags = item.get("tags")
        if isinstance(raw_tags, (list, tuple)):
            tags.extend(str(tag or "").strip() for tag in raw_tags)
        else:
            tags.append(str(item.get("tag") or "").strip())
    context = _birth_journey_intake_basic_context(state)
    age = _birth_journey_context_age(context)
    if age is not None and age >= 40:
        tags.append("age_40_plus")
    return _unique_text_list(tags, 8)


def _birth_journey_personalized_followup_candidates_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or "").strip(): item
        for item in _birth_journey_personalized_followup_queue(state)
        if str(item.get("id") or "").strip()
    }


def _birth_journey_answered_personalized_followups(
    state: dict[str, Any],
    *,
    risk_relevant_only: bool = False,
) -> dict[str, Any]:
    answers = _dict_value(state.get("personalized_followups"))
    if not answers or not risk_relevant_only:
        return answers
    candidates_by_id = _birth_journey_personalized_followup_candidates_by_id(state)
    risk_answers: dict[str, Any] = {}
    for followup_id, answer in answers.items():
        followup = candidates_by_id.get(str(followup_id))
        raw_tags = (followup or {}).get("tags")
        if isinstance(raw_tags, (list, tuple)):
            tags = {str(tag or "").strip() for tag in raw_tags}
        else:
            tags = {str((followup or {}).get("tag") or "").strip()}
        if tags & BIRTH_JOURNEY_RISK_RELEVANT_PERSONALIZATION_TAGS:
            risk_answers[str(followup_id)] = answer
    return risk_answers


def _birth_journey_has_personalized_risk_context(state: dict[str, Any]) -> bool:
    return bool(_birth_journey_answered_personalized_followups(state, risk_relevant_only=True))


def _birth_journey_personalized_answer_text(value: Any) -> str:
    text = _first_answer_text(value)
    if not text:
        return ""
    normalized = _normalized_placeholder(text)
    if normalized in {
        "继续",
        "继续下一步",
        "下一步",
        "跳过",
        "先跳过",
        "忘了",
        "不记得",
        "记不清",
        "记不清楚",
        "不清楚",
        "不确定",
        "按待确认放进计划",
    }:
        return "按待确认放进计划"
    return text


def _birth_journey_personalized_risk_summary(state: dict[str, Any]) -> str:
    answers = _birth_journey_answered_personalized_followups(state, risk_relevant_only=True)
    if not answers:
        return ""
    candidates_by_id = _birth_journey_personalized_followup_candidates_by_id(state)
    parts: list[str] = []
    for followup_id, answer in answers.items():
        followup = candidates_by_id.get(str(followup_id))
        title = str((followup or {}).get("title") or followup_id).strip()
        text = _birth_journey_personalized_answer_text(answer)
        parts.append(f"{title}：{text or '按待确认放进计划'}")
    return "已按个性化情况追问：" + "；".join(parts)


def _birth_journey_personalized_followup_text(value: Any, *followup_ids: str) -> str:
    answers = _dict_value(value)
    if not answers:
        return ""
    texts: list[str] = []
    for followup_id in followup_ids:
        text = _birth_journey_personalized_answer_text(answers.get(followup_id))
        if text:
            texts.append(text)
    return "；".join(_unique_text_list(texts, 6))


def _append_birth_journey_context_text(context: dict[str, Any], key: str, *values: Any) -> None:
    texts = _unique_text_list([context.get(key), *values], 8)
    if texts:
        context[key] = "；".join(texts)


def _birth_journey_text_is_yes(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"是", "yes", "true", "ivf"} or text.startswith("是")


def _birth_journey_is_multiple_pregnancy(value: Any) -> bool:
    text = str(value or "").strip()
    return any(token in text for token in ("双", "多", "三", "multiple", "twins", "triplets"))


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
        "personalized_followup": "基础信息已记录，下一步由模型基于个性化上下文判断是否还需要补充一个核心事实。",
        "checkup_done_question": "基础信息已记录，孕早期先确认是否已经做过产检。",
        "checkup_records_upload": "基础信息已记录，下一步建议上传产检记录；报告不在手边可以先跳过。",
        "final_plan_confirmation": "产检记录步骤已处理，生成计划前最后确认是否还有补充信息。",
        "pause_for_symptoms": "用户报告了需要先处理的当前症状，暂停生成孕期计划。",
        "generate_plan": "孕期计划信息采集已完成，可以调用 birth_journey_plan_card_create。",
    }
    return summaries.get(next_step, "继续推进孕期计划信息采集。")


def _birth_journey_intake_instruction(next_step: str) -> str:
    instructions = {
        "basic_info_form": "最终回复说明基础信息表已打开，并温和解释这是为了后面更贴合用户情况地整理孕期计划；请用户简单填写知道的部分，不确定的地方选择表单里的兜底选项。不要在聊天里逐项追问这些字段。",
        "checkup_done_question": "先用 initial_analysis 对基础信息做 1-2 句承接，然后只确认是否做过产检；如果做过，下一步再建议上传产检报告；如果没做过，可以先跳过报告上传。",
        "checkup_records_upload": "先用 initial_analysis 对基础信息做 1-2 句承接，然后建议用户上传最新一次或目前能找到的产检记录；如果报告不在手边也可以先跳过。",
        "final_plan_confirmation": "生成孕期计划前，只问一句：还有其他需要补充的信息吗？如果没有，我就基于目前的信息开始为你制定孕期计划啦。不要展开计划内容，也不要追加其它问题。",
        "personalized_followup": "基于 personalization_context 自主判断本轮是否还需要追问；如果需要，只选一个最影响计划安排的事实，先承接用户已提供的信息，再问一个具体问题；如果信息已足够、用户表示没有异常或想跳过，调用 finish_personalized_followups。",
        "pause_for_symptoms": "先承接用户情况，建议优先联系医生/医院确认；不要继续生成孕期计划。",
        "generate_plan": "直接调用 birth_journey_plan_card_create，plan_context 使用本工具返回的 plan_context；工具调用前不要先输出路线图。",
    }
    return instructions.get(next_step, "按 next_step 继续推进。")


def _birth_journey_intake_question(next_step: str, plan_context: dict[str, Any], state: dict[str, Any]) -> str:
    if next_step == "checkup_done_question":
        week_text = str(plan_context.get("due_date_or_week") or plan_context.get("current_week") or "").strip()
        prefix = f"你现在是{week_text}，" if week_text else ""
        return prefix + "想先确认一下：到目前为止有没有做过产检？如果做过，下一步建议上传一下产检报告；如果还没做过，也可以直接说还没做过。"
    if next_step == "checkup_records_upload":
        return "如果产检报告在手边，可以上传最近一次或目前能找到的产检记录；如果报告不在手边，也可以先跳过。"
    if next_step == "final_plan_confirmation":
        return "还有其他需要补充的信息吗？如果没有，我就基于目前的信息开始为你制定孕期计划啦。"
    if next_step == "personalized_followup":
        return "基于个性化上下文，只补充一个会影响孕期计划安排的核心事实；如果信息已经够了，可以直接结束个性化追问。"
    if next_step == "pause_for_symptoms":
        return "针对你挡下的这种情况。我建议可以先暂停制定计划，优先按医生或医院建议处理当前症状。"
    return ""


def _birth_journey_context_age(context: dict[str, Any]) -> int | None:
    try:
        age = int(str(context.get("age") or "").strip())
    except (TypeError, ValueError):
        return None
    return age if 12 <= age <= 60 else None


def _birth_journey_initial_analysis(plan_context: dict[str, Any]) -> dict[str, Any]:
    if not _dict_value(plan_context):
        return {}
    week = _birth_journey_context_week(plan_context)
    stage = _birth_journey_stage_from_week(week)
    stage_summary = {
        "early": "你现在处在孕早期，接下来重点是确认孕周、首次产检/建档安排，以及早筛时间窗。",
        "mid": "你现在处在孕中期，接下来重点是中期筛查、系统超声/糖耐等时间窗，以及把复查项目排进日程。",
        "late": "你现在进入孕晚期，接下来重点是胎动、入院信号、分娩方式沟通和待产准备。",
        "unknown": "基础信息已经记录，接下来会先把产检资料和影响计划的关键情况补齐。",
    }.get(stage, "")
    highlights: list[str] = []
    age = _birth_journey_context_age(plan_context)
    if age is not None and age >= 35:
        highlights.append(
            f"我注意到你{age}岁，按产科管理属于“高龄孕产妇”，需要把筛查选择、血压血糖、用药复查和胎儿生长提前放进计划。"
        )
    if _birth_journey_text_is_yes(plan_context.get("ivf")):
        highlights.append("你是 IVF/辅助生殖怀孕，孕周和预产期通常要按移植信息或医生确认口径来对齐。")
    if _birth_journey_is_multiple_pregnancy(plan_context.get("fetus_count")):
        highlights.append("这次是多胎妊娠，产检会更关注胎儿生长、宫颈长度和早产信号。")
    multiple_type = _birth_journey_substantive_text(plan_context.get("multiple_pregnancy_type"))
    if multiple_type and "不适用" not in multiple_type:
        highlights.append(f"你填到{multiple_type}，后续会把多胎类型对应的复查节奏和医生提醒纳入计划。")
    if _normalize_first_birth(_first_text(plan_context.get("first_birth"))) == "是":
        highlights.append("你是第一胎，计划里会把产检节奏、入院流程和临产信号拆得更具体。")
    if _birth_journey_has_prior_c_section(plan_context):
        highlights.append("你有既往剖宫产信息，计划会提前整理上次剖宫产原因、间隔时间和这次分娩方式评估。")
    elif _birth_journey_prior_history_text(plan_context):
        highlights.append("你填了既往孕产情况，计划会把这部分纳入产检复查、分娩沟通和产后准备。")
    if _birth_journey_medical_condition_text(plan_context):
        highlights.append("你填了基础疾病或长期用药，计划会更重视用药确认、复查频率和专科协同。")
    if _birth_journey_doctor_note_text(plan_context):
        highlights.append("你填了医生特殊提醒，计划会优先把复查时间、观察指标和异常联系路径问清。")
    birth_path = _normalize_birth_journey_birth_path(_first_text(plan_context.get("birth_path")))
    if "剖" in birth_path:
        highlights.append("你提到计划剖宫产，孕晚期计划会更关注手术时间、禁食入院、住院天数和伤口护理准备。")
    return {
        "stage": stage,
        "current_week": week,
        "summary": stage_summary,
        "highlights": _unique_text_list(highlights, 5),
    }


def _birth_journey_checkup_report_strategy(state: dict[str, Any]) -> dict[str, str]:
    if not _dict_value(state.get("basic_info")):
        return {}
    week = _birth_journey_intake_week(state)
    stage = _birth_journey_stage_from_week(week)
    if stage == "early" and "checkup_done_confirmed" not in state and not _birth_journey_checkup_step_done(state):
        return {
            "stage": stage,
            "mode": "ask_if_done",
            "message": "孕早期可能还没做过产检，所以先确认是否已经产检；做过再建议上传报告，没做过就先跳过。",
        }
    return {
        "stage": stage,
        "mode": "suggest_upload_or_skip",
        "message": "上传最近一次或目前能找到的产检记录，可以让计划更贴合检查结果；报告不在手边也可以先跳过。",
    }


def birth_journey_intake_quick_reply_guidance(
    next_step: str,
    *,
    personalized_followup: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    if str(next_step or "").strip() == "personalized_followup" and isinstance(personalized_followup, dict):
        options = _birth_journey_personalized_reply_option_texts(personalized_followup)
        if options:
            return [{"text": text} for text in options]
    replies_by_step = {
        "checkup_done_question": ("做过产检", "还没做过", "不确定先跳过"),
        "checkup_records_upload": ("产检记录上传完毕", "先跳过这步", "我现在没有记录"),
        "final_plan_confirmation": ("没有了，开始制定", "我想补充一点", "稍等我再看看"),
        "personalized_followup": ("暂无异常", "还不确定", "我补充一下"),
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
    personalized_followups = _dict_value(state.get("personalized_followups"))
    personalized_records = _birth_journey_personalized_followup_records(state)
    personalization_tags = _birth_journey_personalization_tags(state)
    if personalization_tags:
        context["personalization_tags"] = personalization_tags
    if personalized_followups:
        context["personalized_followups"] = personalized_followups
        _append_birth_journey_context_text(
            context,
            "prior_birth_history",
            _birth_journey_personalized_followup_text(
                personalized_followups,
                "prior_c_section_birth_path_detail",
                "prior_birth_history_detail",
                "prior_preterm_monitoring_detail",
                "prior_adverse_pregnancy_detail",
            ),
        )
        _append_birth_journey_context_text(
            context,
            "medical_notes",
            _birth_journey_personalized_followup_text(
                personalized_followups,
                "hypertension_or_preeclampsia_monitoring",
                "diabetes_or_gdm_monitoring",
                "chronic_medical_condition_coordination",
            ),
        )
        _append_birth_journey_context_text(
            context,
            "doctor_notes",
            _birth_journey_personalized_followup_text(personalized_followups, "doctor_special_notes_followup"),
        )
        _append_birth_journey_context_text(
            context,
            "checkup_status",
            _birth_journey_personalized_followup_text(personalized_followups, "pregnancy_milestone_status_detail"),
        )
        _append_birth_journey_context_text(
            context,
            "birth_hospital",
            _birth_journey_personalized_followup_text(personalized_followups, "care_site_status_detail"),
        )
        _append_birth_journey_context_text(
            context,
            "doctor_notes",
            _birth_journey_personalized_followup_text(personalized_followups, "gestational_week_basis_detail"),
        )
    if personalized_records:
        context["personalized_followup_records"] = personalized_records
        personalized_facts = "；".join(
            " / ".join(
                part
                for part in (
                    record.get("topic"),
                    record.get("answer"),
                    record.get("plan_impact"),
                )
                if part
            )
            for record in personalized_records
        )
        if personalized_facts:
            context["personalized_facts"] = personalized_facts
            _append_birth_journey_context_text(context, "doctor_notes", personalized_facts)
    for key in (
        "risk_factors",
        "current_symptoms",
        "lifestyle_context",
        "feeding_ibclc_context",
        "feeding_intention",
        "multiple_pregnancy_type",
        "previous_birth_method",
        "previous_c_section_count",
        "prior_birth_history",
        "medical_notes",
        "doctor_notes",
    ):
        if key in state:
            context[key] = state[key]
    final_additional_info = _first_answer_text(state.get("final_additional_info"))
    if final_additional_info and _normalized_placeholder(final_additional_info) not in PLACEHOLDER_VALUES:
        context["final_additional_info"] = final_additional_info
        if _birth_journey_final_info_looks_medical(final_additional_info):
            _append_birth_journey_context_text(context, "risk_factors", final_additional_info)
        elif _birth_journey_final_info_looks_lifestyle(final_additional_info):
            _append_birth_journey_context_text(context, "lifestyle_context", final_additional_info)
    if _has_meaningful_value(state.get("entry_reason")) and not _birth_journey_is_generic_plan_request(_first_answer_text(state.get("entry_reason"))):
        context["entry_reason"] = state["entry_reason"]
    initial_concerns = state.get("initial_concerns")
    if isinstance(initial_concerns, list) and initial_concerns:
        specific_concerns = [
            concern
            for concern in initial_concerns
            if _has_meaningful_value(concern) and not _birth_journey_is_generic_plan_request(str(concern))
        ]
        if specific_concerns:
            context["initial_concerns"] = specific_concerns
    entry_followup = _first_answer_text(state.get("entry_concern_followup"))
    entry_context_text = _birth_journey_entry_context_text(state)
    if entry_followup and not _birth_journey_is_generic_plan_request(entry_followup):
        context["entry_concern_followup"] = entry_followup
    if entry_context_text and not _birth_journey_is_generic_plan_request(entry_context_text):
        context["top_worries"] = _first_answer_text(entry_followup, initial_concerns, state.get("entry_reason"))
    if entry_followup and not _birth_journey_is_generic_plan_request(entry_followup):
        existing_lifestyle = _first_answer_text(context.get("lifestyle_context"))
        context["lifestyle_context"] = (
            f"{existing_lifestyle}；前期关键担心：{entry_followup}" if existing_lifestyle else entry_followup
        )
    return context


def _birth_journey_final_info_looks_medical(text: str) -> bool:
    return any(
        token in text
        for token in (
            "血压",
            "血糖",
            "糖耐",
            "甲状腺",
            "基础病",
            "免疫",
            "用药",
            "复查",
            "胎盘",
            "低置",
            "前置",
            "羊水",
            "偏多",
            "偏少",
            "宫颈",
            "宫缩",
            "胎动",
            "早产",
            "流产",
            "出血",
            "破水",
            "腹痛",
            "贫血",
            "高危",
            "风险",
            "异常",
            "临界",
        )
    )


def _birth_journey_final_info_looks_lifestyle(text: str) -> bool:
    return any(token in text for token in ("工作", "通勤", "久坐", "久站", "睡眠", "失眠", "压力", "家里", "支持", "照顾"))


def _birth_journey_due_date_from_lmp(value: Any) -> str:
    lmp = _date_from_text(str(value or ""))
    if lmp is None:
        return ""
    return _format_birth_journey_date(lmp + timedelta(days=280))


def _missing_birth_journey_required_context(form_data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in BIRTH_JOURNEY_SURVEY_FIELDS:
        if field["id"] == "basic_info":
            if _birth_journey_missing_required_basic_info_fields(form_data):
                missing.append(field["id"])
            continue
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
    city_or_country = _first_text(form_data.get("city_or_country"), form_data.get("birth_prep_city_or_country"), form_data.get("city"), form_data.get("country"))
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
    personalized_followups = _dict_value(form_data.get("personalized_followups"))
    personalized_followup_records = (
        [item for item in form_data.get("personalized_followup_records") if isinstance(item, dict)]
        if isinstance(form_data.get("personalized_followup_records"), list)
        else []
    )
    personalized_facts = _first_answer_text(form_data.get("personalized_facts"))
    prior_birth_history = _text_list(
        _first_answer_text(
            form_data.get("prior_birth_history"),
            form_data.get("pregnancy_history_or_notes"),
            _birth_journey_personalized_followup_text(
                personalized_followups,
                "prior_c_section_birth_path_detail",
                "prior_birth_history_detail",
                "prior_preterm_monitoring_detail",
                "prior_adverse_pregnancy_detail",
            ),
        )
    )
    medical_notes = _text_list(
        _first_answer_text(
            form_data.get("medical_notes"),
            form_data.get("chronic_conditions"),
            form_data.get("long_term_medication"),
            _birth_journey_personalized_followup_text(
                personalized_followups,
                "hypertension_or_preeclampsia_monitoring",
                "diabetes_or_gdm_monitoring",
                "chronic_medical_condition_coordination",
            ),
        )
    )
    doctor_notes = _text_list(
        _first_answer_text(
            form_data.get("doctor_notes"),
            form_data.get("special_notes"),
            form_data.get("abnormal_results"),
            _birth_journey_personalized_followup_text(personalized_followups, "doctor_special_notes_followup"),
        )
    )
    if personalized_facts:
        doctor_notes.extend(_text_list(personalized_facts))
    final_additional_info = _first_answer_text(form_data.get("final_additional_info"))
    risk_factors = _unique_text_list(
        form_data.get("risk_factors")
        or form_data.get("high_risk_factors")
        or (final_additional_info if _birth_journey_final_info_looks_medical(final_additional_info) else ""),
        8,
    )
    age = _first_text(form_data.get("age"), form_data.get("birth_prep_age"))
    multiple_pregnancy_type = _first_text(form_data.get("multiple_pregnancy_type"), form_data.get("birth_prep_multiple_pregnancy_type"))
    previous_birth_method = _first_answer_text(form_data.get("previous_birth_method"), form_data.get("birth_prep_previous_birth_method"))
    previous_c_section_count = _first_answer_text(
        form_data.get("previous_c_section_count"),
        form_data.get("birth_prep_previous_c_section_count"),
    )
    top_worries = _birth_journey_specific_concern_text(_first_answer_text(form_data.get("top_worries"), form_data.get("birth_prep_top_worries")))
    entry_reason = _birth_journey_specific_concern_text(
        _first_answer_text(
            form_data.get("entry_reason"),
            form_data.get("initial_message"),
            form_data.get("user_message"),
        )
    )
    initial_concerns = [
        concern
        for concern in _unique_text_list(
        form_data.get("initial_concerns") or form_data.get("concerns"),
        6,
        )
        if not _birth_journey_is_generic_plan_request(concern)
    ]
    entry_concern_followup = _birth_journey_specific_concern_text(_first_answer_text(form_data.get("entry_concern_followup")))
    lifestyle_context = _first_answer_text(
        form_data.get("lifestyle_context"),
        form_data.get("work_context"),
        form_data.get("sleep_context"),
        form_data.get("exercise_context"),
        form_data.get("family_support"),
        final_additional_info if _birth_journey_final_info_looks_lifestyle(final_additional_info) else "",
        form_data.get("budget"),
    )
    feeding_ibclc_context = _first_answer_text(
        form_data.get("feeding_ibclc_context"),
        form_data.get("pump_plan"),
        form_data.get("ibclc_plan"),
        form_data.get("lactation_history"),
    )
    context = {
        "first_birth": first_birth,
        "fetus_count": fetus_count,
        "multiple_pregnancy_type": multiple_pregnancy_type,
        "birth_path": birth_path,
        "previous_birth_method": previous_birth_method,
        "previous_c_section_count": previous_c_section_count,
        "prior_birth_history": prior_birth_history,
        "feeding_intention": feeding_intention,
        "feeding_ibclc_context": feeding_ibclc_context,
        "birth_setting": birth_setting,
        "city_or_country": city_or_country,
        "support_person": support_person,
        "checkup_status": checkup_status,
        "current_symptoms": current_symptoms,
        "risk_factors": risk_factors,
        "age": age,
        "top_worries": top_worries,
        "entry_reason": entry_reason,
        "initial_concerns": initial_concerns,
        "entry_concern_followup": entry_concern_followup,
        "final_additional_info": final_additional_info,
        "lifestyle_context": lifestyle_context,
        "medical_notes": medical_notes,
        "doctor_notes": doctor_notes,
        "personalized_followups": personalized_followups,
        "personalized_followup_records": personalized_followup_records,
        "personalized_facts": personalized_facts,
        "current_week": timeline.get("current_week"),
    }
    phases = [_birth_journey_phase_payload(spec, context) for spec in timeline["phase_specs"]]
    _mark_birth_journey_current_phase(phases)
    todo_plan = _birth_journey_todo_plan(timeline, context, phases)
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
        "todo_engine_version": BIRTH_JOURNEY_TODO_ENGINE_VERSION,
        "title": "孕期计划",
        "subtitle": _birth_journey_subtitle(timeline, scope),
        "owner": owner,
        "todo_plan": todo_plan,
        "generation_context": _birth_journey_generation_context(timeline, context, todo_plan),
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


def _birth_journey_todo_period_model_items(todo_plan: dict[str, Any] | None, index: int) -> list[dict[str, Any]]:
    periods = todo_plan.get("periods") if isinstance(todo_plan, dict) else []
    if not isinstance(periods, list) or index >= len(periods):
        return []
    period = periods[index]
    if not isinstance(period, dict):
        return []
    items = period.get("items") if isinstance(period.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def _birth_journey_next_7_view_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    view_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        view_item = dict(item)
        source_item_id = str(view_item.get("id") or "").strip()
        if source_item_id:
            view_item["source_item_id"] = source_item_id
        view_item.pop("id", None)
        view_items.append(view_item)
    return view_items


def _birth_journey_todo_plan(
    timeline: dict[str, Any],
    context: dict[str, Any],
    phases: list[dict[str, Any]],
) -> dict[str, Any]:
    week = timeline.get("current_week")
    cadence, cadence_label, _span_weeks, _max_periods = _birth_journey_todo_cadence(week)
    current_phase = next((phase for phase in phases if isinstance(phase, dict) and phase.get("status") == "current"), phases[0] if phases else {})
    periods = _birth_journey_todo_periods(week)
    period_payloads = []
    for index, period in enumerate(periods):
        period_cadence = str(period.get("granularity") or cadence).strip() or cadence
        items = _birth_journey_todo_period_items(
            period.get("week_start"),
            period.get("week_end"),
            period_cadence,
            context,
            current_phase,
            index,
        )
        if not items:
            continue
        period_payloads.append(
            {
                **period,
                "granularity": period_cadence,
                "display_mode": period.get("display_mode") or ("expanded" if index == 0 else "collapsed"),
                "status": period.get("status") or ("current" if index == 0 else "upcoming"),
                "subtitle": str(period.get("subtitle") or "").strip()
                or _birth_journey_todo_period_subtitle(period.get("week_start"), period.get("week_end")),
                "items": items,
            }
        )
    if not period_payloads:
        period_payloads = [
            {
                "id": "period_01",
                "title": "补齐孕周后生成清单",
                "week_start": None,
                "week_end": None,
                "granularity": cadence,
                "display_mode": "expanded",
                "status": "current",
                "subtitle": "先补充孕周或预产期，再把产检窗口、身体变化和生产准备排成可执行事项。",
                "items": _birth_journey_todo_select_items(
                    _birth_journey_condition_todo_items(context, "补齐孕周后生成清单"),
                    _birth_journey_todo_item_limit(cadence),
                ),
            }
        ]
    return {
        "title": "孕期 To do list",
        "current_week": week,
        "cadence": cadence,
        "cadence_label": cadence_label,
        "cadence_reason": _birth_journey_todo_cadence_reason(week, cadence_label),
        "route_summary": _birth_journey_todo_route_summary(week),
        "periods": period_payloads,
    }


def _birth_journey_generation_context(
    timeline: dict[str, Any],
    context: dict[str, Any],
    todo_plan: dict[str, Any],
) -> dict[str, Any]:
    periods = todo_plan.get("periods") if isinstance(todo_plan.get("periods"), list) else []
    compact_periods: list[dict[str, Any]] = []
    for period in periods:
        if not isinstance(period, dict):
            continue
        period_items = period.get("items") if isinstance(period.get("items"), list) else []
        compact_periods.append(
            {
                "id": str(period.get("id") or "").strip(),
                "title": str(period.get("title") or "").strip(),
                "week_start": period.get("week_start"),
                "week_end": period.get("week_end"),
                "granularity": str(period.get("granularity") or "").strip(),
                "display_mode": str(period.get("display_mode") or "").strip(),
                "status": str(period.get("status") or "").strip(),
                "subtitle": str(period.get("subtitle") or "").strip(),
                "items": [_birth_journey_generation_context_item(item) for item in period_items if isinstance(item, dict)],
            }
        )
    return {
        "current_week": timeline.get("current_week"),
        "cadence": str(todo_plan.get("cadence") or "").strip(),
        "cadence_label": str(todo_plan.get("cadence_label") or "").strip(),
        "cadence_reason": str(todo_plan.get("cadence_reason") or "").strip(),
        "route_summary": str(todo_plan.get("route_summary") or "").strip(),
        "personalization_basis": _birth_journey_plan_condition_labels(context, max_labels=8),
        "periods": compact_periods,
    }


def _birth_journey_generation_context_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "priority_type": str(item.get("priority_type") or "").strip(),
        "priority_label": str(item.get("priority_label") or "").strip(),
        "timeframe": str(item.get("timeframe") or "").strip(),
        "reason": str(item.get("plan_reason") or item.get("why_for_you") or item.get("reason") or "").strip(),
        "source_tags": [str(tag or "").strip() for tag in item.get("source_tags") or [] if str(tag or "").strip()],
        "steps": [str(step or "").strip() for step in item.get("steps") or [] if str(step or "").strip()],
        "after_done_value": str(item.get("after_done_value") or "").strip(),
    }


def _birth_journey_todo_cadence(week: Any) -> tuple[str, str, int, int]:
    if not isinstance(week, int):
        return "monthly", "按月计划", 4, 3
    if week >= 36:
        return "weekly", "每周计划", 1, 5
    if week >= 28:
        return "biweekly", "双周计划", 2, 4
    return "monthly", "按月计划", 4, 3


def _birth_journey_todo_cadence_reason(week: Any, cadence_label: str) -> str:
    if not isinstance(week, int):
        return "补齐孕周后，再按当前阶段选择按月、双周或每周推进。"
    if week >= 36:
        return "36 周后产检和临产信号更密集，适合每周逐项确认。"
    if week >= 28:
        return "进入孕晚期后，产检、胎动观察和入院准备变密，适合按双周推进。"
    return f"孕 {week} 周阶段适合先按 4 周窗口推进，把检查、复查和生活安排分块完成。"


def _birth_journey_todo_route_summary(week: Any) -> str:
    if not isinstance(week, int):
        return "补齐孕周后，会按当前阶段生成从现在到住院生产的路线。"
    if week < 28:
        return f"从孕 {week} 周开始，先按月推进，孕晚期改成双周，36 周后按周收口到住院生产。"
    if week < 36:
        return f"从孕 {week} 周开始，先按双周推进，36 周后按周收口到住院生产。"
    return f"从孕 {week} 周开始，按每周产检和临产信号一路收口到住院生产。"


def _birth_journey_todo_periods(week: Any) -> list[dict[str, Any]]:
    if not isinstance(week, int):
        return [
            {
                "id": "period_01",
                "title": "补齐孕周后生成清单",
                "week_start": None,
                "week_end": None,
                "granularity": "monthly",
                "display_mode": "expanded",
                "status": "current",
                "subtitle": "先补充孕周或预产期，再把产检窗口、身体变化和生产准备排成可执行事项。",
            }
        ]
    periods: list[dict[str, Any]] = []
    start_week = max(1, min(40, week))
    max_week = 40
    index = 0
    while start_week <= max_week:
        index += 1
        granularity, span_weeks = _birth_journey_period_granularity_and_span(start_week)
        end_week = min(max_week, start_week + span_weeks - 1)
        if start_week < 28:
            end_week = min(end_week, 27)
        elif start_week < 36:
            end_week = min(end_week, 35)
        else:
            end_week = start_week
        periods.append(
            {
                "id": f"period_{index:02d}",
                "title": _birth_journey_todo_period_title(start_week, end_week),
                "week_start": start_week,
                "week_end": end_week,
                "granularity": granularity,
                "display_mode": "expanded" if index == 1 else "collapsed",
                "status": "current" if index == 1 else "upcoming",
            }
        )
        start_week = end_week + 1
    periods.append(
        {
            "id": "period_terminal",
            "title": "临产与住院生产",
            "week_start": None,
            "week_end": None,
            "granularity": "terminal",
            "display_mode": "terminal",
            "status": "terminal",
            "subtitle": "把临产信号、医院入口、证件报告和陪同分工收口。",
        }
    )
    if periods:
        return periods
    return [
        {
            "id": "period_01",
            "title": _birth_journey_todo_period_title(week, week),
            "week_start": week,
            "week_end": week,
            "granularity": "weekly",
            "display_mode": "expanded",
            "status": "current",
        },
        {
            "id": "period_terminal",
            "title": "临产与住院生产",
            "week_start": None,
            "week_end": None,
            "granularity": "terminal",
            "display_mode": "terminal",
            "status": "terminal",
            "subtitle": "把临产信号、医院入口、证件报告和陪同分工收口。",
        },
    ]


def _birth_journey_period_granularity_and_span(start_week: int) -> tuple[str, int]:
    if start_week >= 36:
        return "weekly", 1
    if start_week >= 28:
        return "biweekly", 2
    return "monthly", 4


def _birth_journey_todo_period_title(start_week: Any, end_week: Any) -> str:
    if isinstance(start_week, int) and isinstance(end_week, int):
        if start_week == end_week:
            return f"孕 {start_week} 周"
        return f"孕 {start_week}-{end_week} 周"
    return "补齐孕周后生成清单"


def _birth_journey_todo_period_subtitle(start_week: Any, end_week: Any) -> str:
    if not isinstance(start_week, int):
        return "先补充孕周或预产期，再把产检窗口、身体变化和生产准备排成可执行事项。"
    if start_week <= 13:
        return "先确认妊娠位置、胎心胎芽、建档材料和 NT/早筛窗口。"
    if start_week <= 19:
        return "重点接上中期筛查，并提前锁定大排畸预约。"
    if start_week <= 23:
        return "把大排畸、看报告和复查时间接起来。"
    if start_week <= 27:
        return "重点完成糖耐、血常规/尿常规和血压体重等复查。"
    if start_week <= 31:
        return "开始固定胎动、血压、水肿和胎儿生长观察节奏。"
    if start_week <= 35:
        return "把胎位、宝宝生长、GBS 和入院材料一步步准备好。"
    return "围绕每周产检、胎动、宫缩、破水、见红和入院联系步骤确认。"


def _birth_journey_base_todo_catalog() -> tuple[dict[str, Any], ...]:
    return (
        {
            "id": "week_01_05_first_check",
            "week_start": 1,
            "week_end": 5,
            "title": "做首次产检信息准备",
            "reason": "首次就诊前先把孕周口径、验孕结果、用药和补剂准备好，医生更容易判断下一步检查。",
            "steps": [
                "记录末次月经日期、验孕时间和验孕结果",
                "收集验孕试纸、抽血结果及已有检查报告",
                "整理正在服用的药物和叶酸/维生素补剂",
                "确认首次产检时间、科室和入口并完成预约",
                "准备身份证、医保卡和既往病史资料",
            ],
            "done_criteria": "已准备好孕周口径、验孕结果、当前用药补剂和首次就诊安排。",
            "source_tags": ["week_1_5", "dating"],
        },
        {
            "id": "week_06_08_location_heartbeat",
            "week_start": 6,
            "week_end": 8,
            "title": "做首次B超检查",
            "reason": "这个阶段不只是做 B 超，还要知道结果什么时候看、异常时按哪个入口联系医院。",
            "steps": [
                "确认B超时间、地点及是否需要憋尿",
                "携带验孕/抽血结果及身份证件到院检查",
                "完成检查后确认报告获取时间与方式",
                "查看是否提示宫内妊娠、胎心胎芽等关键结果",
                "保存医院咨询入口或复查联系方式",
            ],
            "done_criteria": "已安排 B 超、设置报告回看提醒，并保存异常联系入口。",
            "source_tags": ["week_6_8", "ultrasound"],
        },
        {
            "id": "week_08_10_booking_materials",
            "week_start": 8,
            "week_end": 10,
            "title": "做建档材料准备",
            "reason": "建档前把证件、已有报告、病史和补剂信息集中好，能减少临时补材料和重复跑医院。",
            "steps": [
                "整理身份证、医保卡、就诊卡等基础证件",
                "按时间整理B超、抽血、尿检及病史资料",
                "记录过敏史、基础病史及长期用药情况",
                "记录叶酸及其他补剂名称与剂量",
                "按医院要求补齐缺失材料",
            ],
            "done_criteria": "已形成一份建档材料包，下次产检能直接带走。",
            "source_tags": ["week_8_10", "booking"],
        },
        {
            "id": "week_11_13_nt_screening",
            "week_start": 11,
            "week_end": 13,
            "title": "做NT/早筛检查安排",
            "reason": "NT 和早孕筛查有时间窗口，要同时记下日期、地点和报告回看时间。",
            "steps": [
                "确认NT检查时间在孕周窗口内",
                "确认是否同天进行早筛抽血及空腹要求",
                "预约检查时间并写入日历",
                "保存NT数值及筛查结果",
                "设置复查或随访提醒",
            ],
            "done_criteria": "已安排 NT/早筛日期、地点、当天准备和报告回看提醒。",
            "source_tags": ["week_11_13", "nt_screening"],
        },
        {
            "id": "week_15_20_mid_screening",
            "week_start": 15,
            "week_end": 20,
            "title": "做唐筛/无创/羊穿决策",
            "reason": "这几周要把唐筛、无创 DNA 或羊穿落实到检查日期、取报告时间和异常结果复诊安排。",
            "steps": [
                "携带NT及既往筛查结果参与产检决策",
                "确认适合唐筛、无创DNA或羊穿方案",
                "如做抽血筛查，确认时间与出报告方式",
                "如需羊穿，记录预约窗口及术前要求",
                "设置结果回看与后续处理提醒",
            ],
            "done_criteria": "已确定中期筛查方式，并写下检查日期、取报告时间和异常结果复诊方式。",
            "source_tags": ["week_15_20", "mid_screening"],
        },
        {
            "id": "week_18_22_anomaly_scan",
            "week_start": 18,
            "week_end": 22,
            "title": "做大排畸检查",
            "reason": "大排畸通常要提前排队，检查前把时间、地点、陪同和复查入口都安排好。",
            "steps": [
                "确认检查时间、地点及预计时长",
                "提前准备产检资料与既往报告",
                "安排当天交通与陪同人员",
                "完成检查并确认胎儿结构、羊水、胎盘结果",
                "保存复查入口及后续安排",
            ],
            "done_criteria": "已安排大排畸预约、当天陪同交通和复查入口。",
            "source_tags": ["week_18_22", "anomaly_scan"],
        },
        {
            "id": "week_20_24_anomaly_report",
            "week_start": 20,
            "week_end": 24,
            "title": "做大排畸结果复查确认",
            "reason": "报告出来后，要把胎盘、羊水、胎儿结构提示和是否复查变成明确的下一步安排。",
            "steps": [
                "拍照保存报告并带给医生复看",
                "确认胎儿结构、胎盘、羊水及宫颈情况",
                "标记需复查或随访的项目",
                "确认复查时间或是否无需复查",
                "将复查时间加入日历",
            ],
            "done_criteria": "已完成报告回看，并明确是否需要复查及复查时间。",
            "source_tags": ["week_20_24", "report_review"],
        },
        {
            "id": "week_24_28_gtt",
            "week_start": 24,
            "week_end": 28,
            "title": "做糖耐检查（OGTT）",
            "reason": "糖耐检查当天要连续处理预约、空腹、喝糖水、多次抽血和检查后进食，提前排清楚会更稳。",
            "steps": [
                "确认检查时间并完成预约（如需预约制提前锁定）",
                "检查前8-12小时禁食禁水，确保空腹",
                "到院完成空腹抽血并饮用75g葡萄糖水",
                "按1小时/2小时（部分3小时）完成抽血",
                "检查结束后进食并观察身体反应",
            ],
            "dedupe_after_current": True,
            "done_criteria": "已确认糖耐预约、空腹要求、喝糖水和抽血节点，并安排检查后第一餐。",
            "source_tags": ["week_24_28", "gtt"],
        },
        {
            "id": "week_28_31_fetal_movement",
            "week_start": 28,
            "week_end": 31,
            "title": "做胎动与异常观察",
            "reason": "进入晚孕期后，胎动、血压、体重和水肿需要固定观察，不对劲时也要知道联系谁。",
            "steps": [
                "选择每天固定时间观察胎动",
                "记录胎动明显增减变化",
                "观察头痛、水肿、腹痛、出血或流水",
                "保存医院急诊与产科联系方式",
                "出现异常及时联系医院",
            ],
            "done_criteria": "已定好每天看胎动的时间，并保存不对劲时联系医院的方式。",
            "source_tags": ["week_28_31", "movement_monitoring"],
        },
        {
            "id": "week_29_31_next_checkup",
            "week_start": 29,
            "week_end": 31,
            "title": "做下一次产检安排",
            "reason": "这几周要把下次产检、胎儿生长、血压体重和尿常规复查提前排上。",
            "steps": [
                "确认下次产检时间与挂号方式",
                "记录检查项目（B超/胎监/抽血等）",
                "确认是否需要空腹或特殊准备",
                "产检前准备既往报告与记录",
                "产检后更新下一次计划",
            ],
            "done_criteria": "已安排下次产检、复查项目提醒和当天要带材料。",
            "source_tags": ["week_29_31", "next_checkup"],
        },
        {
            "id": "week_32_35_position_growth",
            "week_start": 32,
            "week_end": 35,
            "title": "做胎位与生长复查",
            "reason": "这几周要把胎位、胎儿生长、羊水胎盘和是否需要复查变成明确安排。",
            "steps": [
                "确认胎位、羊水及胎儿生长复查时间",
                "保存胎位及估重结果",
                "记录是否需要进一步观察或干预",
                "确认复查频率",
                "加入日历提醒",
            ],
            "done_criteria": "已明确胎位、生长评估、复查项目和下次产检间隔。",
            "source_tags": ["week_32_35", "growth_position"],
        },
        {
            "id": "week_35_37_gbs_admission",
            "week_start": 35,
            "week_end": 37,
            "title": "做GBS筛查与入院准备",
            "reason": "临近足月前，要把 GBS、产检报告、证件材料和入院入口一起准备好。",
            "steps": [
                "确认GBS筛查时间并完成采样",
                "保存GBS结果并标记阴性/阳性",
                "整理住院证件及关键产检资料",
                "确认入院入口及联系电话",
                "准备陪护与住院规则信息",
            ],
            "done_criteria": "已安排 GBS，备好证件报告，并保存医院入口。",
            "source_tags": ["week_35_37", "gbs_admission"],
        },
        {
            "id": "week_38_40_labor_signal",
            "week_start": 38,
            "week_end": 40,
            "title": "做临产入院准备与联系流程",
            "reason": "足月后要把宫缩、破水、见红和胎动变化时的医院联系口径放在手机里。",
            "steps": [
                "保存产科/急诊/夜间入院联系方式",
                "明确宫缩、破水、见红的处理方式",
                "确认入院路线与交通方式",
                "准备待产资料袋并固定放置",
                "与陪同人确认分工与行动顺序",
            ],
            "done_criteria": "已保存医院联系方式，并和陪同人确认出发分工。",
            "source_tags": ["week_38_40", "labor_signal"],
        },
    )


def _birth_journey_catalog_week_items(
    start_week: int,
    end_week: int,
    context: dict[str, Any],
    *,
    include_started_before: bool = True,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    period_title = _birth_journey_todo_period_title(start_week, end_week)
    for spec in _birth_journey_base_todo_catalog():
        spec_start = int(spec["week_start"])
        spec_end = int(spec["week_end"])
        if not include_started_before and spec.get("dedupe_after_current") is True and spec_start < start_week:
            continue
        if start_week <= spec_end and end_week >= spec_start:
            items.append(_birth_journey_catalog_item(spec, period_title, context))
    return items


def _birth_journey_catalog_item(spec: dict[str, Any], timeframe: str, context: dict[str, Any]) -> dict[str, Any]:
    return _birth_journey_plan_item(
        str(spec.get("title") or ""),
        str(spec.get("reason") or ""),
        timeframe,
        list(spec.get("based_on") or ["current_week", "checkup_window"]),
        item_id=str(spec.get("id") or ""),
        priority_type=str(spec.get("priority_type") or BIRTH_JOURNEY_PRIORITY_ESSENTIAL),
        source_tags=list(spec.get("source_tags") or []),
        context=context,
        steps=[str(item or "") for item in spec.get("steps") or []],
        step_limit=_birth_journey_catalog_step_limit(spec),
        done_criteria=str(spec.get("done_criteria") or ""),
        after_done_value=str(spec.get("after_done_value") or ""),
    )


def _birth_journey_catalog_step_limit(spec: dict[str, Any]) -> int | None:
    raw = spec.get("step_limit")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _birth_journey_condition_todo_items(context: dict[str, Any], timeframe: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    age = _birth_journey_context_age(context)
    if age is not None and age >= 35:
        items.append(
            _birth_journey_plan_item(
                "高龄风险产检补充",
                "高龄孕期要提前问清筛查路径、血压血糖、胎儿生长、胎盘羊水和复查频率。",
                timeframe,
                ["age"],
                item_id="condition_age_35_plus",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["age_35_plus"],
                context=context,
                steps=[
                    "将无创DNA/羊穿方案纳入产检决策",
                    "确认血压、血糖、尿蛋白复查频率",
                    "设置胎儿生长与羊水复查计划",
                    "记录异常指标及随访规则",
                    "更新产检关注指标",
                ],
                done_criteria="已问清高龄相关筛查、血压血糖和胎儿生长复查安排。",
            )
        )
    if _birth_journey_text_is_yes(context.get("ivf")):
        items.append(
            _birth_journey_plan_item(
                "IVF孕期管理",
                "IVF 怀孕要把孕周口径、黄体支持或其他用药复查和产检时间放到同一份日历里。",
                timeframe,
                ["ivf"],
                item_id="condition_ivf",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["ivf"],
                context=context,
                steps=[
                    "校准孕周与预产期",
                    "记录黄体支持及用药方案",
                    "设置停药/减量提醒",
                    "每次产检携带用药记录",
                    "禁止自行调整用药",
                ],
                done_criteria="已把孕周、当前用药、复查和调药提醒加进日历。",
            )
        )
    if _birth_journey_is_multiple_pregnancy(context.get("fetus_count")):
        items.append(
            _birth_journey_plan_item(
                "多胎管理",
                "双胎妊娠的产检节奏更细，重点是双胎类型、每个宝宝生长、宫颈长度和早产信号。",
                timeframe,
                ["fetus_count", "multiple_pregnancy_type"],
                item_id="condition_multiple",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["multiple_pregnancy"],
                context=context,
                steps=[
                    "设置更密集产检与胎监计划",
                    "记录宫颈长度与早产风险",
                    "设置早产预警信号",
                    "保存急诊联系方式",
                ],
                done_criteria="已确认双胎类型、生长复查频率、宫颈监测和早产联系入口。",
            )
        )
    if _birth_journey_substantive_text(context.get("checkup_status")):
        items.append(
            _birth_journey_plan_item(
                "把没做完的产检安排上",
                "已做检查、未预约项目、报告异常和复查要求要落到下一次产检或日程提醒里。",
                timeframe,
                ["checkup_status"],
                item_id="condition_checkup_status",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["checkup_status"],
                context=context,
                steps=[
                    "把已完成、未预约、等结果和需复查项目分成四类",
                    "给未预约项目补上日期、地点和是否需要空腹",
                    "给需复查项目写下复查原因、时间窗口和报告携带要求",
                    "把等结果的项目设置报告回看提醒",
                    "下次产检带着这张清单逐项确认",
                ],
                done_criteria="已给没做完的项目定好日期或放到下次产检里。",
            )
        )
    if _birth_journey_substantive_text(context.get("risk_factors")) and not (
        _birth_journey_medical_condition_text(context) or _birth_journey_doctor_note_text(context)
    ):
        items.append(
            _birth_journey_plan_item(
                "做胎动与风险观察",
                "明确风险点要变成日常观察、风险触发条件和医院联系入口。",
                timeframe,
                ["risk_factors"],
                item_id="condition_risk_factors",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["risk_factors"],
                context=context,
                steps=[
                    "固定时间观察胎动变化",
                    "记录异常症状变化趋势",
                    "保存急诊联系入口",
                    "设置风险触发条件",
                    "出现异常及时联系医院",
                ],
                done_criteria="已把明确风险点对应到复查指标、日期和异常联系规则。",
            )
        )
    first_birth = _normalize_first_birth(_first_text(context.get("first_birth")))
    if first_birth == "是":
        items.append(
            _birth_journey_plan_item(
                "定好从产检到入院怎么走",
                "第一胎容易卡在流程陌生，先把产检节奏、入院入口和临产处理变成一步步能做的安排。",
                timeframe,
                ["first_birth"],
                item_id="condition_first_birth",
                priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
                source_tags=["first_birth"],
                context=context,
                steps=[
                    "保存当前阶段的产检频率和下一次产检项目",
                    "把医院建档、产检、急诊和入院入口分开记清楚",
                    "写下规律宫缩、破水、见红分别先做什么",
                    "把待产资料袋和重要证件固定放在同一位置",
                    "把这套流程发给陪同人一起过一遍",
                ],
                done_criteria="已保存产检频率、医院入口和临产时的联系步骤。",
            )
        )
    elif first_birth == "否" and not _birth_journey_has_prior_c_section(context):
        items.append(
            _birth_journey_plan_item(
                "把上一胎经历用到这次准备",
                "上一胎方式和恢复经历会影响这次分娩沟通、产后支持和复查安排。",
                timeframe,
                ["first_birth", "prior_birth_history"],
                item_id="condition_prior_birth",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["prior_birth"],
                context=context,
                steps=[
                    "写下上一胎主要分娩方式和当时最影响体验的一件事",
                    "如果有早产、出血、撕裂或感染，单独标出来",
                    "把这些经历放进下次产检沟通清单",
                    "根据上一胎恢复难点提前安排产后支持",
                    "和陪同人同步这次想避开或重点准备的事项",
                ],
                done_criteria="已把上一胎经历转成这次产检沟通和产后支持准备。",
            )
        )
    if _birth_journey_has_prior_c_section(context):
        items.append(
            _birth_journey_plan_item(
                "剖宫产/分娩准备资料",
                "上次剖宫产原因、间隔时间和手术记录会影响这次分娩方式评估。",
                timeframe,
                ["previous_birth_method", "previous_c_section_count", "prior_birth_history"],
                item_id="condition_prior_c_section",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["prior_c_section"],
                context=context,
                steps=[
                    "整理既往剖宫产手术资料",
                    "记录胎盘位置与间隔情况",
                    "准备术前沟通要点",
                    "预留分娩方式决策时间",
                    "设置产前评估提醒",
                ],
                done_criteria="已备好剖宫产史资料，并安排分娩方式评估沟通。",
            )
        )
    if _birth_journey_medical_condition_text(context):
        items.append(
            _birth_journey_plan_item(
                "基础病管理",
                "基础疾病或长期用药要和产科复查、专科复查放到同一份日程里。",
                timeframe,
                ["medical_notes"],
                item_id="condition_medical",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["medical_notes"],
                context=context,
                steps=[
                    "记录基础病及用药情况",
                    "设置专科与产科复查联动",
                    "记录关键监测指标",
                    "更新异常处理规则",
                    "保存联系路径",
                ],
                done_criteria="已把用药确认、专科复查和异常联系办法加到产检安排里。",
            )
        )
    if _birth_journey_doctor_note_text(context):
        items.append(
            _birth_journey_plan_item(
                "把医生提醒设成复查和观察提醒",
                "医生特别提醒过的内容，要落到复查日期、日常观察和异常联系步骤里。",
                timeframe,
                ["doctor_notes"],
                item_id="condition_doctor_notes",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["doctor_notes"],
                context=context,
                steps=[
                    "把医生提醒原话写下来，并标出对应的检查或观察点",
                    "确认每条提醒对应的复查日期、地点和报告要求",
                    "把需要每天观察的变化设置成提醒",
                    "写下哪些表现需要当天联系医院",
                    "复查后把医生的新要求继续更新进计划",
                ],
                done_criteria="已把医生提醒设成复查日期、观察提醒和异常时联系医院的方式。",
            )
        )
    birth_path = _birth_journey_substantive_text(context.get("birth_path"))
    if "剖" in birth_path:
        items.append(
            _birth_journey_plan_item(
                "剖宫产/分娩准备资料",
                "计划剖宫产会影响术前检查、禁食入院、住院照护和伤口护理。",
                timeframe,
                ["birth_path"],
                item_id="condition_planned_c_section",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["planned_c_section"],
                context=context,
                steps=[
                    "整理既往剖宫产手术资料",
                    "记录胎盘位置与间隔情况",
                    "准备术前沟通要点",
                    "预留分娩方式决策时间",
                    "设置产前评估提醒",
                ],
                done_criteria="已安排术前检查、禁食入院、住院照护和伤口观察事项。",
            )
        )
    elif any(token in birth_path for token in ("顺", "阴道")):
        items.append(
            _birth_journey_plan_item(
                "做顺产临产联系准备",
                "计划顺产要把宫缩、破水、见红后的联系口径和镇痛/陪产规则提前确认。",
                timeframe,
                ["birth_path"],
                item_id="condition_planned_vaginal",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["planned_vaginal_birth"],
                context=context,
                steps=[
                    "保存规律宫缩/破水/见红处理方式",
                    "确认无痛分娩与陪产规则",
                    "设置入院路线与电话",
                    "记录分娩沟通偏好",
                    "提前同步陪同人",
                ],
                done_criteria="已备好临产联系步骤、镇痛陪产规则和陪同人沟通偏好。",
            )
        )
    if _birth_journey_substantive_text(context.get("birth_setting")):
        items.append(
            _birth_journey_plan_item(
                "做住院材料与沟通准备",
                "生产医院不同，证件、报告、入院入口和分娩沟通重点要提前放在一起。",
                timeframe,
                ["birth_setting"],
                item_id="condition_birth_hospital",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["birth_hospital"],
                context=context,
                steps=[
                    "整理身份证、医保卡及产检资料",
                    "按顺序整理关键检查与报告",
                    "记录过敏史及长期用药",
                    "写清分娩偏好与沟通重点",
                    "入院前再次核对医院要求",
                ],
                done_criteria="已确认预登记、入院入口、证件材料和陪产探视规则。",
            )
        )
    if _birth_journey_substantive_text(context.get("lifestyle_context")):
        items.append(
            _birth_journey_lifestyle_next_7_item(
                _birth_journey_substantive_text(context.get("lifestyle_context")),
                timeframe,
                context,
            )
        )
    if _birth_journey_support_text(context.get("support_person")):
        items.append(
            _birth_journey_plan_item(
                "做陪同人分工确认",
                "临产时需要有人负责联系医院、拿材料、出发路线和同步医生口径。",
                timeframe,
                ["support_person"],
                item_id="condition_support_person",
                priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
                source_tags=["support_person"],
                context=context,
                steps=[
                    "明确联系医院负责人",
                    "明确资料与证件携带负责人",
                    "明确交通与导航负责人",
                    "汇总所有联系方式与入口",
                    "临产前统一复核流程",
                ],
                done_criteria="已把联系医院、拿材料、出发路线和同步医嘱分工发给支持人确认。",
            )
        )
    week = context.get("current_week")
    if (
        _birth_journey_substantive_text(context.get("feeding_intention"))
        or _birth_journey_substantive_text(context.get("feeding_ibclc_context"))
    ) and (not isinstance(week, int) or week >= 32):
        items.append(
            _birth_journey_plan_item(
                "定好产后48小时喂养求助方式",
                "住院最初两天最需要提前知道亲喂、混合喂养、泵奶和求助入口。",
                timeframe,
                ["feeding_intention", "feeding_ibclc_context"],
                item_id="condition_feeding",
                priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
                source_tags=["feeding"],
                context=context,
                steps=[
                    "写下产后 48 小时优先亲喂、混合还是泵奶",
                    "准备住院时要问的含乳、吸吮、补奶和泵奶问题",
                    "保存医院护士、母乳门诊或 IBCLC 咨询入口",
                    "把乳头疼痛、涨奶、宝宝尿布和体重变化列为观察点",
                    "和家人同步不要随意加奶或用奶嘴的沟通口径",
                ],
                done_criteria="已准备产后 48 小时喂养方案和求助入口。",
            )
        )
    return _unique_birth_journey_plan_items(items)


def _birth_journey_todo_period_items(
    start_week: Any,
    end_week: Any,
    cadence: str,
    context: dict[str, Any],
    current_phase: dict[str, Any],
    period_index: int,
) -> list[dict[str, Any]]:
    period_title = _birth_journey_todo_period_title(start_week, end_week)
    if cadence == "terminal":
        return _birth_journey_terminal_todo_items(context)
    if not isinstance(start_week, int):
        return _birth_journey_todo_select_items(
            _birth_journey_condition_todo_items(context, period_title),
            _birth_journey_todo_item_limit(cadence),
        )

    candidates: list[dict[str, Any]] = []
    if period_index == 0:
        candidates.extend(_birth_journey_safety_items(context))
    catalog_items: list[dict[str, Any]] = []
    if isinstance(end_week, int):
        catalog_items = _birth_journey_catalog_week_items(
            start_week,
            end_week,
            context,
            include_started_before=period_index == 0,
        )
    if period_index == 0:
        condition_items = _birth_journey_condition_todo_items(context, period_title)
        catalog_take = 1 if len(condition_items) >= 4 else 2 if len(condition_items) >= 3 else len(catalog_items)
        candidates.extend(catalog_items[:catalog_take])
        candidates.extend(condition_items)
        candidates.extend(catalog_items[catalog_take:])
    else:
        candidates.extend(catalog_items)
    normalized = [
        _birth_journey_todo_item_with_period(item, period_title)
        for item in _unique_birth_journey_plan_items(candidates)
    ]
    return _birth_journey_todo_select_items(normalized, _birth_journey_todo_item_limit(cadence))


def _birth_journey_todo_item_limit(cadence: str) -> int:
    if cadence == "terminal":
        return 3
    if cadence == "monthly":
        return 5
    return 4


def _birth_journey_terminal_todo_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    timeframe = "临产与住院生产"
    items = [
        _birth_journey_plan_item(
            "做临产入院准备与联系流程",
            "临近生产时，宫缩、破水、见红和胎动变化都需要提前知道联系谁、走哪个入口。",
            timeframe,
            ["current_week", "birth_hospital", "support_person"],
            item_id="terminal_labor_admission",
            priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
            source_tags=["terminal", "labor_signal", "hospital_entry"],
            context=context,
            steps=[
                "保存产科/急诊/夜间入院联系方式",
                "明确宫缩、破水、见红的处理方式",
                "确认入院路线与交通方式",
                "准备待产资料袋并固定放置",
                "与陪同人确认分工与行动顺序",
            ],
            after_done_value="临产时不用临时翻信息，能更快联系医院并出发。",
        ),
        _birth_journey_plan_item(
            "做住院材料与沟通准备",
            "入院前把证件、产检报告和分娩沟通重点集中好，护士和医生接手会更顺。",
            timeframe,
            ["birth_hospital", "birth_path", "checkup_status"],
            item_id="terminal_hospital_materials",
            priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
            source_tags=["terminal", "admission_materials"],
            context=context,
            steps=[
                "整理身份证、医保卡及产检资料",
                "按顺序整理关键检查与报告",
                "记录过敏史及长期用药",
                "写清分娩偏好与沟通重点",
                "入院前再次核对医院要求",
            ],
            after_done_value="入院办理和医生沟通会更省心，重要报告不容易漏带。",
        ),
    ]
    if _birth_journey_substantive_text(context.get("support_person")):
        items.append(
            _birth_journey_plan_item(
                "做陪同人分工确认",
                "临产当天往往比较紧张，提前分好电话、路线、材料和陪护安排会减少现场混乱。",
                timeframe,
                ["support_person", "birth_hospital"],
                item_id="terminal_support_split",
                priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
                source_tags=["terminal", "support_person"],
                context=context,
                steps=[
                    "明确联系医院负责人",
                    "明确资料与证件携带负责人",
                    "明确交通与导航负责人",
                    "汇总所有联系方式与入口",
                    "临产前统一复核流程",
                ],
                after_done_value="陪同人知道自己负责什么，临产当天更容易配合。",
            )
        )
    return _birth_journey_todo_select_items(_unique_birth_journey_plan_items(items), _birth_journey_todo_item_limit("terminal"))


def _birth_journey_todo_item_with_period(item: dict[str, Any], period_title: str) -> dict[str, Any]:
    next_item = dict(item)
    next_item["period"] = period_title
    timeframe = str(next_item.get("timeframe") or "").strip()
    if timeframe in {"本周", "今天", "今天或明天", "未来 7 天", "未来 2-4 周"}:
        next_item["timeframe"] = period_title
    return next_item


def _birth_journey_todo_select_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    essential = [
        item
        for item in items
        if isinstance(item, dict) and item.get("priority_type") == BIRTH_JOURNEY_PRIORITY_ESSENTIAL
    ]
    supportive = [
        item
        for item in items
        if isinstance(item, dict) and item.get("priority_type") != BIRTH_JOURNEY_PRIORITY_ESSENTIAL
    ]
    selected = [*essential[:limit], *supportive[: max(0, limit - len(essential[:limit]))]]
    return selected[:limit]


BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS = 22
BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS = 140
BIRTH_JOURNEY_PLAN_ITEM_STEP_MAX_CHARS = 88
BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS = 96
BIRTH_JOURNEY_PLAN_ITEM_DEFAULT_STEP_LIMIT = 5
BIRTH_JOURNEY_PLAN_ITEM_MAX_STEP_LIMIT = 6
BIRTH_JOURNEY_TODO_ENGINE_VERSION = "actionable_steps_v2"
BIRTH_JOURNEY_STALE_VISIBLE_TITLES = (
    "准备第一次看医生要带的信息",
    "约好B超，也记得看结果",
    "把建档材料放在一起",
    "约好NT/早筛并设置提醒",
    "把中期筛查预约排上",
    "约好大排畸当天安排",
    "看完大排畸，定好是否复查",
    "每天固定看胎动和不舒服",
    "定好下一次晚孕产检提醒",
    "定好胎位和宝宝生长复查",
    "做好GBS检查和入院材料",
    "定好临产时怎么去医院",
    "把高龄产检重点问清楚",
    "把IVF复查加进日历",
    "定好双胎每次怎么复查",
    "准备上次剖宫产资料给医生看",
    "把基础病复查放进产检日历",
    "定好顺产临产怎么联系医院",
    "把临产分工发给家人确认",
    "定好临产后怎么联系医院",
    "把住院材料和沟通重点放一起",
    "和陪同人过一遍入院分工",
    "先确认是否需要联系医院或医生",
    "固定通勤后的 20 分钟休息",
    "设置久坐后的起身提醒",
    "今晚固定睡前 30 分钟降噪",
    "安排久站后的坐下休息点",
    "把未完成产检项排进计划",
    "建立胎动和异常联系机制",
    "把高龄监测纳入产检节奏",
    "把医生提醒落到观察日程",
    "准备剖宫产史评估资料",
    "完成剖宫产术前准备路径",
    "完成B超安排和回看闭环",
    "完成GBS和入院材料收口",
    "设置临产出发方案",
    "完成生产医院入院流程确认",
    "安排好糖耐当天怎么做",
)
BIRTH_JOURNEY_PRIORITY_ESSENTIAL = "essential"
BIRTH_JOURNEY_PRIORITY_SUPPORTIVE = "supportive"
BIRTH_JOURNEY_PRIORITY_LABELS = {
    BIRTH_JOURNEY_PRIORITY_ESSENTIAL: "重要",
    BIRTH_JOURNEY_PRIORITY_SUPPORTIVE: "建议",
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


def _birth_journey_reason_without_priority_prefix(reason: Any) -> str:
    text = str(reason or "").strip()
    for label in BIRTH_JOURNEY_PRIORITY_LABELS.values():
        prefix = f"{label}｜"
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _birth_journey_visible_priority_title(title: Any, priority_type: str) -> str:
    base_title = _birth_journey_title_without_priority_prefix(title)
    return _truncate_birth_journey_plan_text(base_title, BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS)


def _birth_journey_legacy_next_7_item_upgrade(title: str) -> dict[str, Any]:
    upgrades: dict[str, dict[str, Any]] = {
        "确认高龄孕期关注重点": {
            "title": "高龄风险产检补充",
            "steps": ["将无创DNA/羊穿方案纳入产检决策", "确认血压、血糖、尿蛋白复查频率", "设置胎儿生长与羊水复查计划"],
            "done_criteria": "已问清高龄相关筛查、血压血糖和胎儿生长复查安排。",
            "after_done_value": "做完后，高龄相关复查会更清楚，不会只停留在下次再问。",
        },
        "确认高龄孕期监测安排": {
            "title": "定好高龄相关复查提醒",
            "steps": ["确认血压血糖怎么观察", "安排宝宝监测或复查时间", "保存异常时当天联系谁"],
            "done_criteria": "已把观察频率、异常提醒和医院联系方式加到日历里。",
            "after_done_value": "做完后，你会知道哪些变化要观察、什么时候复查、异常时联系谁。",
        },
        "确认血压血糖监测安排": {
            "title": "定好血压血糖怎么测",
            "steps": ["确定在家监测频率和时间", "保存异常阈值和处理方式", "安排下次复查或带记录时间"],
            "done_criteria": "已定好什么时候测、什么情况要联系医院、什么时候复查。",
            "after_done_value": "做完后，你会知道每天要不要测、测到什么数值要联系医院。",
        },
        "补齐下次产检时间": {
            "title": "安排下一次产检提醒",
            "steps": ["确认下次产检日期和地点", "把检查项目加入日历提醒", "准备是否空腹和需带材料"],
            "done_criteria": "已安排日期、地点、检查项目、是否空腹和需带材料提醒。",
            "after_done_value": "做完后，你会知道接下来一周围绕哪次产检准备，不会临近才发现漏预约。",
        },
        "整理产检报告里的待确认项": {
            "title": "把报告里要复查的事安排上",
            "steps": ["找出报告里要复查或未预约的项目", "给项目定日期或放到下次产检", "准备下次产检要确认的重点"],
            "done_criteria": "已给报告里要复查的事定好日期或放到下次产检里。",
            "after_done_value": "做完后，下次产检会更聚焦，不容易把复查安排留空。",
        },
        "给生活压力留缓冲": {
            "title": "做久坐/久站/疲劳管理",
            "steps": ["设置定时休息与起身提醒", "每45-60分钟活动2-3分钟", "记录疲劳与水肿情况"],
            "done_criteria": "已设置休息提醒，并开始记录疲劳或水肿变化。",
            "after_done_value": "做完后，休息和活动会变成固定节奏，也更容易判断不适是否需要问医生。",
        },
        "拆开最焦虑的三件事": {
            "title": "把焦虑拆成 3 个可处理问题",
            "steps": ["选出最担心的 3 件事", "标出需要医生确认的一件", "安排今天能处理的一件"],
            "done_criteria": "已把担心分成医生确认、自己安排、家人支持三类。",
            "after_done_value": "做完后，焦虑会变成可提问、可安排、可求助的清单。",
        },
        "把宝宝情况问题列给医生": {
            "title": "定好宝宝情况要不要复查",
            "steps": ["选出最担心的宝宝变化", "确认宝宝生长或胎动怎么观察", "安排是否需要复查或额外观察"],
            "done_criteria": "已明确宝宝情况、复查需求和日常观察方式。",
            "after_done_value": "做完后，你会少靠猜测判断宝宝情况，知道接下来观察什么。",
        },
        "把最担心的问题列成三条": {
            "title": "把担心的事拆成今天能做的事",
            "steps": ["选出 3 个最影响执行的担心点", "标出最需要医生确认的一条", "把一件自己能安排的事放进日程"],
            "done_criteria": "已把担心分成医生确认、自己安排、家人支持三类。",
            "after_done_value": "做完后，下一次沟通会更省力，也不容易漏掉真正担心的点。",
        },
        "把未完成产检项排进计划": {
            "title": "把没做完的产检安排上",
            "reason": "重要｜已做检查、未预约项目、报告异常和复查要求要落到下一次产检或日程提醒里。",
            "steps": ["找出还没预约或要复查的项目", "给每个项目定日期或放到下次产检", "准备下次产检要确认的重点"],
            "after_done_value": "做完后，没做完的项目会有日期或下次产检入口，不会悬着。",
        },
        "建立胎动和异常联系机制": {
            "title": "做胎动与异常观察",
            "reason": "重要｜进入晚孕期后，胎动、血压、体重和水肿需要固定观察，不对劲时也要知道联系谁。",
            "steps": ["选择每天固定时间观察胎动", "记录胎动明显增减变化", "出现异常及时联系医院"],
            "after_done_value": "做完后，你会有固定观察时间，也知道不对劲时联系谁。",
        },
        "把高龄监测纳入产检节奏": {
            "title": "高龄风险产检补充",
            "reason": "重要｜高龄孕期要提前问清筛查路径、血压血糖、胎儿生长、胎盘羊水和复查频率。",
            "steps": ["将无创DNA/羊穿方案纳入产检决策", "确认血压、血糖、尿蛋白复查频率", "设置胎儿生长与羊水复查计划"],
            "after_done_value": "做完后，高龄相关复查会更清楚，不会只停留在下次再问。",
        },
        "把医生提醒落到观察日程": {
            "title": "把医生提醒设成复查和观察提醒",
            "reason": "重要｜医生特别提醒过的内容，要落到复查日期、日常观察和异常联系步骤里。",
            "steps": ["确认提醒对应的复查日期", "设置日常观察提醒", "保存异常时联系医院的方式"],
            "after_done_value": "做完后，医生提醒会变成复查日期、观察提醒和联系入口。",
        },
        "准备剖宫产史评估资料": {
            "title": "剖宫产/分娩准备资料",
            "reason": "重要｜上次剖宫产原因、间隔时间和手术记录会影响这次分娩方式评估。",
            "steps": ["整理既往剖宫产手术资料", "记录胎盘位置与间隔情况", "设置产前评估提醒"],
            "after_done_value": "做完后，医生评估这次分娩方式时会有更完整资料。",
        },
        "完成剖宫产术前准备路径": {
            "title": "剖宫产/分娩准备资料",
            "reason": "重要｜计划剖宫产会影响术前检查、禁食入院、住院照护和伤口护理。",
            "steps": ["整理既往剖宫产手术资料", "准备术前沟通要点", "设置产前评估提醒"],
            "after_done_value": "做完后，术前检查、入院和术后照护会有明确安排。",
        },
        "完成B超安排和回看闭环": {
            "title": "做首次B超检查",
            "reason": "重要｜这个阶段不只是做 B 超，还要知道结果什么时候看、异常时按哪个入口联系医院。",
            "steps": ["确认B超时间、地点及是否需要憋尿", "完成检查后确认报告获取时间与方式", "保存医院咨询入口或复查联系方式"],
            "after_done_value": "做完后，B 超日期、报告回看和异常联系入口都会清楚。",
        },
        "完成GBS和入院材料收口": {
            "title": "做GBS筛查与入院准备",
            "reason": "重要｜临近足月前，要把 GBS、产检报告、证件材料和入院入口一起准备好。",
            "steps": ["确认GBS筛查时间并完成采样", "保存GBS结果并标记阴性/阳性", "确认入院入口及联系电话"],
            "after_done_value": "做完后，GBS、证件报告和医院入口都会准备好。",
        },
        "设置临产出发方案": {
            "title": "做临产入院准备与联系流程",
            "reason": "重要｜足月后要把宫缩、破水、见红和胎动变化时的医院联系口径放在手机里。",
            "steps": ["保存产科/急诊/夜间入院联系方式", "明确宫缩、破水、见红的处理方式", "与陪同人确认分工与行动顺序"],
            "after_done_value": "做完后，你和陪同人会知道临产时怎么联系和出发。",
        },
        "完成生产医院入院流程确认": {
            "title": "做住院材料与沟通准备",
            "reason": "重要｜生产医院不同，预登记、夜间入口、陪产探视和证件要求也会不同。",
            "steps": ["整理身份证、医保卡及产检资料", "按顺序整理关键检查与报告", "入院前再次核对医院要求"],
            "after_done_value": "做完后，你会知道生产医院的入院入口和材料要求。",
        },
        "把IVF复查加进日历": {
            "title": "IVF孕期管理",
            "reason": "重要｜IVF 怀孕要把孕周口径、用药复查和产检时间放到同一份日历里。",
            "steps": ["校准孕周与预产期", "记录黄体支持及用药方案", "设置停药/减量提醒"],
            "after_done_value": "做完后，孕周、用药和复查提醒会对齐。",
        },
        "定好双胎每次怎么复查": {
            "title": "多胎管理",
            "reason": "重要｜多胎计划要提前把产检频率、宫颈长度和早产信号放进预案。",
            "steps": ["设置更密集产检与胎监计划", "记录宫颈长度与早产风险", "保存急诊联系方式"],
            "after_done_value": "做完后，多胎复查和早产预警会更清楚。",
        },
        "确认基础疾病和用药复查": {
            "title": "基础病管理",
            "reason": "重要｜基础疾病或长期用药要和产科复查、专科复查放到同一份日程里。",
            "steps": ["记录基础病及用药情况", "设置专科与产科复查联动", "保存联系路径"],
            "after_done_value": "做完后，用药确认、专科复查和异常联系办法会更集中。",
        },
        "和陪同人过一遍入院分工": {
            "title": "做陪同人分工确认",
            "reason": "建议｜临产当天提前分好电话、路线、材料和陪护安排会减少现场混乱。",
            "steps": ["明确联系医院负责人", "明确资料与证件携带负责人", "明确交通与导航负责人"],
            "after_done_value": "做完后，陪同人知道自己负责什么，临产当天更容易配合。",
        },
    }
    aliases = {
        "约好B超，也记得看结果": "完成B超安排和回看闭环",
        "每天固定看胎动和不舒服": "建立胎动和异常联系机制",
        "把高龄产检重点问清楚": "确认高龄孕期关注重点",
        "准备上次剖宫产资料给医生看": "准备剖宫产史评估资料",
        "把基础病复查放进产检日历": "确认基础疾病和用药复查",
        "做好GBS检查和入院材料": "完成GBS和入院材料收口",
        "定好临产时怎么去医院": "设置临产出发方案",
        "定好临产后怎么联系医院": "设置临产出发方案",
        "把住院材料和沟通重点放一起": "完成生产医院入院流程确认",
        "确认生产医院怎么入院": "完成生产医院入院流程确认",
        "把临产分工发给家人确认": "和陪同人过一遍入院分工",
        "和陪同人过一遍入院分工": "和陪同人过一遍入院分工",
        "设置久坐后的起身提醒": "给生活压力留缓冲",
        "固定通勤后的 20 分钟休息": "给生活压力留缓冲",
        "安排久站后的坐下休息点": "给生活压力留缓冲",
        "问清高龄孕期 3 个检查重点": "确认高龄孕期关注重点",
        "问清高龄孕期 3 个监测重点": "确认高龄孕期关注重点",
        "下次产检问清高龄监测 3 件事": "确认高龄孕期监测安排",
        "问清血压血糖监测规则": "确认血压血糖监测安排",
        "今天补齐下次产检日期和项目": "补齐下次产检时间",
        "从产检报告圈出 3 个待确认点": "整理产检报告里的待确认项",
        "把最大生活压力拆成 1 个动作": "给生活压力留缓冲",
        "把担心点整理成 3 个医生问题": "把最担心的问题列成三条",
    }
    normalized_title = str(title or "").strip()
    upgrade = upgrades.get(normalized_title) or upgrades.get(aliases.get(normalized_title, ""))
    if not upgrade:
        return {}
    result = dict(upgrade)
    result_title = str(result.get("title") or normalized_title).strip()
    if "steps" not in result:
        result["steps"] = _birth_journey_plan_item_steps(result_title, [])
    result["completion_followup"] = str(result.get("after_done_value") or "").strip()
    return result


def _birth_journey_plan_item(
    title: str,
    reason: str,
    timeframe: str,
    based_on: list[str] | None = None,
    *,
    item_id: str | None = None,
    priority_type: str | None = None,
    source_tags: list[str] | None = None,
    context: dict[str, Any] | None = None,
    steps: list[str] | None = None,
    step_limit: int | None = None,
    done_criteria: str | None = None,
    after_done_value: str | None = None,
) -> dict[str, Any]:
    clean_title = _truncate_birth_journey_plan_text(title, BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS)
    basis = based_on or []
    clean_base_reason = _birth_journey_plan_reason_text(reason)
    safe_step_limit = _birth_journey_plan_item_step_limit(step_limit)
    clean_steps = [
        _truncate_birth_journey_plan_text(step, BIRTH_JOURNEY_PLAN_ITEM_STEP_MAX_CHARS)
        for step in (steps or _birth_journey_plan_item_steps(title, basis))[:safe_step_limit]
        if str(step or "").strip()
    ]
    clean_after_done_value = _truncate_birth_journey_plan_text(
        after_done_value or _birth_journey_after_done_value(title, basis),
        BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS,
    )
    if priority_type not in BIRTH_JOURNEY_PRIORITY_LABELS:
        priority_type, priority_label = _birth_journey_plan_item_priority(title, basis)
    else:
        priority_label = BIRTH_JOURNEY_PRIORITY_LABELS[priority_type]
    reason_payload = _birth_journey_plan_item_reason_payload(
        clean_base_reason,
        context or {},
        basis,
        timeframe=timeframe,
        title=clean_title,
        source_tags=source_tags or [],
    )
    clean_plan_reason = _truncate_birth_journey_plan_text(
        str(reason_payload.get("plan_reason") or ""),
        BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS,
    )
    clean_display_reason = _truncate_birth_journey_plan_text(
        str(reason_payload.get("display_reason") or ""),
        BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS,
    )
    display_reason = (
        _truncate_birth_journey_plan_text(
            f"{priority_label}｜{clean_display_reason}",
            BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS,
        )
        if clean_display_reason
        else ""
    )
    return {
        "id": str(item_id or "").strip() or None,
        "title": clean_title,
        "reason": display_reason,
        "timeframe": timeframe,
        "based_on": basis,
        "source_tags": _unique_text_list([*(source_tags or []), *basis], 8),
        "priority_type": priority_type,
        "priority_label": priority_label,
        "why_for_you": clean_display_reason,
        "plan_reason": clean_plan_reason,
        "reason_type": str(reason_payload.get("reason_type") or "standard"),
        "hide_reason": bool(reason_payload.get("hide_reason")),
        "steps": clean_steps,
        "after_done_value": clean_after_done_value,
        "completion_followup": _birth_journey_completion_followup(title, clean_after_done_value),
        "completed": False,
        "completed_at": None,
        "completed_source": None,
    }


def _birth_journey_plan_item_step_limit(value: int | None) -> int:
    if value is None:
        return BIRTH_JOURNEY_PLAN_ITEM_DEFAULT_STEP_LIMIT
    return max(1, min(BIRTH_JOURNEY_PLAN_ITEM_MAX_STEP_LIMIT, int(value)))


def _birth_journey_plan_item_priority(title: str, based_on: list[str]) -> tuple[str, str]:
    text = str(title or "")
    essential_keys = {
        "current_week",
        "checkup_status",
        "checkup_window",
        "current_symptoms",
        "risk_factors",
        "medical_notes",
        "doctor_notes",
        "prior_birth_history",
        "previous_birth_method",
        "previous_c_section_count",
        "multiple_pregnancy_type",
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
        "stage_attention",
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
        "医生提醒",
        "监测",
        "高龄",
        "血压",
        "血糖",
        "胎盘",
        "羊水",
        "宫颈",
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
        steps = [
            "确认糖耐预约日期、地点和是否需要提前取号",
            "按医院口径设置禁食禁水开始时间",
            "到院后按空腹、喝糖水、1 小时、2 小时顺序完成抽血",
            "检查期间静坐等待，不进食、不喝含糖饮料",
            "检查结束后及时进食，并设置报告回看提醒",
        ]
    elif any(token in text for token in ("B超", "NT", "早筛")):
        steps = [
            "确认检查日期在医院要求的孕周或复查窗口内",
            "提前问清检查地点、到院时间和是否需要空腹或憋尿",
            "检查当天带上产检本、身份证件和既往报告",
            "拿到报告后保存关键数值、结论和医生备注",
            "把报告回看、复查或转诊提醒加进日历",
        ]
    elif "大排畸" in text:
        steps = [
            "确认大排畸预约日期、地点和预计检查时长",
            "检查前准备产检本、既往 B 超和筛查报告",
            "检查时重点问清胎儿结构、胎盘、羊水和宫颈结论",
            "拿到报告后圈出建议复查、随访或观察的内容",
            "把复查日期或无需复查结论加进日历",
        ]
    elif "GBS" in text:
        steps = [
            "确认 GBS 筛查日期、地点和采样方式",
            "拿到结果后拍照保存，并标记阳性或阴性结论",
            "把证件、产检本和关键报告放进待产资料袋",
            "确认医院白天、夜间和急诊入院入口",
            "把陪产、探视和住院材料要求写进清单",
        ]
    elif any(token in text for token in ("产检", "报告", "复查", "医生", "风险", "监测", "高龄", "血压", "血糖")):
        steps = [
            "把相关报告、检查结果和医生备注放到同一份资料里",
            "圈出还没预约、等结果或需要复查的项目",
            "给每个项目补上日期、地点和是否需要空腹",
            "把下次产检要确认的问题写成 3 条以内",
            "产检后立刻把新增复查时间更新进日历",
        ]
    elif any(token in text for token in ("胎动", "水肿", "观察")):
        steps = [
            "选一个每天固定观察胎动和身体变化的时间",
            "记录胎动明显变多、变少或和平时不一样的情况",
            "同时留意头痛、视物模糊、水肿、腹痛、出血或流水",
            "保存医院产科、急诊或线上联系入口",
            "出现明显异常时按医院口径当天联系",
        ]
    elif any(token in text for token in ("医院", "入院", "证件", "待产", "入口", "流程")) and not any(token in text for token in ("剖", "顺产", "分娩")):
        steps = [
            "确认医院预登记、入院办理和夜间急诊入口",
            "整理身份证件、医保材料、产检本和关键报告",
            "把医院电话、地址、停车点或打车定位存到手机",
            "确认陪产、探视、待产包和病房用品要求",
            "把入口和出发规则发给陪同人确认",
        ]
    elif any(token in text for token in ("支持", "分工", "家人", "伴侣")):
        steps = [
            "列出需要支持人负责的事项",
            "指定谁负责联系医院、拿材料和带待产包",
            "指定谁负责路线、停车、打车或照看家里事务",
            "把医院入口、电话和备用方案发给支持人",
            "约定临产或住院当天的第一步动作",
        ]
    elif any(token in text for token in ("喂养", "母乳", "混合", "泵奶", "背奶", "IBCLC", "含乳", "涨奶")):
        steps = [
            "写下产后 48 小时优先亲喂、混合还是泵奶",
            "准备住院时要问的含乳、吸吮、补奶和泵奶问题",
            "保存护士、母乳门诊或 IBCLC 求助入口",
            "记录尿布、体重、乳头疼痛和涨奶这些观察点",
            "把喂养偏好提前同步给家人",
        ]
    elif any(token in text for token in ("剖宫产", "剖")):
        steps = [
            "确认术前检查、麻醉评估和入院日期",
            "按医院口径设置禁食禁水提醒",
            "整理证件、报告和既往剖宫产或手术资料",
            "和陪同人分好术后下床、取物和联系医生的事",
            "写下伤口观察、排气进食和复查要求",
        ]
    elif any(token in text for token in ("顺产", "宫缩", "破水", "见红", "分娩")):
        steps = [
            "写下宫缩、破水、见红和胎动异常时分别怎么处理",
            "保存产科、急诊和夜间入院联系方式",
            "确认无痛分娩、陪产和待产室规则",
            "把分娩沟通偏好写成一页纸",
            "把出发分工和医院定位发给陪同人",
        ]
    elif any(token in text for token in ("生活", "睡眠", "通勤", "久坐", "久站", "压力", "休息")):
        steps = [
            "选出今天最影响执行的一件生活压力点",
            "把它拆成 15 分钟内能完成的小动作",
            "给这个动作定一个具体开始时间",
            "告诉支持人你需要的一个具体帮助",
            "完成后把下一步放到明天，避免今天堆太多",
        ]
    elif any(token in text for token in ("焦虑", "担心", "担忧")) or any(key in based_on for key in ("entry_reason", "top_worries", "entry_concern_followup")):
        steps = [
            "写下最影响执行的 3 件担心",
            "标出哪一件需要医生确认",
            "标出哪一件今天自己能先安排",
            "把需要家人支持的一件事说清楚",
            "下次产检前把医生问题整理成 3 条以内",
        ]
    else:
        steps = [
            "把这项拆成一个今天能开始的具体动作",
            "写下要找谁确认、什么时候确认",
            "准备执行时需要的报告、证件或联系入口",
            "把执行时间加进日历或提醒",
            "完成后记录结果，并把下一步放进计划",
        ]
    return [
        _truncate_birth_journey_plan_text(step, BIRTH_JOURNEY_PLAN_ITEM_STEP_MAX_CHARS)
        for step in steps[:BIRTH_JOURNEY_PLAN_ITEM_DEFAULT_STEP_LIMIT]
    ]


def _birth_journey_done_criteria(title: str) -> str:
    text = str(title or "")
    if "糖耐" in text:
        criteria = "已安排糖耐日期、禁食提醒、抽血流程和报告回看方式。"
    elif any(token in text for token in ("产检", "报告", "复查", "医生", "风险", "监测", "高龄", "血压", "血糖")):
        criteria = "已给要复查的事定好日期或放到下次产检里。"
    elif any(token in text for token in ("胎动", "水肿", "观察")):
        criteria = "已定好每天观察时间，并保存不对劲时联系医院的方式。"
    elif any(token in text for token in ("医院", "入院", "证件", "待产", "入口", "流程")) and not any(token in text for token in ("剖", "顺产", "分娩")):
        criteria = "已确认入院入口、证件材料、陪产探视和联系方式。"
    elif any(token in text for token in ("支持", "分工", "家人", "伴侣")):
        criteria = "已明确谁负责联系医院、拿材料、出发和同步医嘱。"
    elif any(token in text for token in ("喂养", "母乳", "混合", "泵奶", "背奶", "IBCLC", "含乳", "涨奶")):
        criteria = "已准备产后 48 小时喂养方案和住院求助入口。"
    elif any(token in text for token in ("焦虑", "担心", "担忧")):
        criteria = "已把担心拆成医生确认、自己安排和家人支持三类。"
    else:
        criteria = "已知道下一步要问谁、什么时候做、做到什么算完成。"
    return _truncate_birth_journey_plan_text(criteria, BIRTH_JOURNEY_PLAN_ITEM_VALUE_MAX_CHARS)


def _birth_journey_after_done_value(title: str, based_on: list[str]) -> str:
    text = str(title or "")
    if "糖耐" in text:
        value = "做完后，我可以继续帮你把糖耐当天流程和结果回看拆成下一步。"
    elif any(token in text for token in ("产检", "报告", "复查", "医生", "风险", "监测", "高龄", "血压", "血糖")):
        value = "做完后，我可以继续帮你把下次产检沟通重点排成清单。"
    elif any(token in text for token in ("胎动", "水肿", "观察")):
        value = "做完后，我可以帮你生成一张异常情况联系卡。"
    elif any(token in text for token in ("医院", "入院", "证件", "待产", "入口", "流程")) and not any(token in text for token in ("剖", "顺产", "分娩")):
        value = "做完后，我可以继续帮你核对入院流程确认清单或待产包。"
    elif any(token in text for token in ("支持", "分工", "家人", "伴侣")):
        value = "做完后，我可以帮你生成临产支持人分工清单。"
    elif any(token in text for token in ("喂养", "母乳", "混合", "泵奶", "背奶", "IBCLC", "含乳", "涨奶")):
        value = "做完后，我可以帮你生成住院后 48 小时喂养和 IBCLC 求助问题。"
    elif any(token in text for token in ("剖宫产", "剖", "顺产", "宫缩", "破水", "见红", "分娩")):
        value = "做完后，我可以继续帮你生成分娩沟通单。"
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
    previous = ""
    while text and text != previous:
        previous = text
        for prefix in ("目的是让你知道：", "目的是让你知道:", "目的是：", "目的是:", "目的是"):
            if text.startswith(prefix):
                text = text[len(prefix) :].strip(" ，,")
                break
    return text.replace("，目的是", "，").replace("；目的是", "；").strip()


def _birth_journey_personalized_plan_item_reason(
    reason: str,
    context: dict[str, Any],
    based_on: list[str],
    *,
    timeframe: str = "",
    title: str = "",
    source_tags: list[str] | None = None,
) -> str:
    return str(
        _birth_journey_plan_item_reason_payload(
            reason,
            context,
            based_on,
            timeframe=timeframe,
            title=title,
            source_tags=source_tags,
        ).get("plan_reason")
        or ""
    )


def _birth_journey_plan_item_reason_payload(
    reason: str,
    context: dict[str, Any],
    based_on: list[str],
    *,
    timeframe: str = "",
    title: str = "",
    source_tags: list[str] | None = None,
) -> dict[str, Any]:
    clean_reason = _birth_journey_plan_reason_text(reason)
    if clean_reason.startswith(("考虑到你", "结合你", "针对你")):
        return {
            "plan_reason": clean_reason,
            "display_reason": clean_reason,
            "reason_type": "personalized",
            "hide_reason": False,
        }
    user_labels, period_label = _birth_journey_plan_item_reason_basis(
        context,
        based_on,
        timeframe=timeframe,
        title=title,
        source_tags=source_tags,
        max_labels=3,
    )
    if user_labels:
        prefix = f"考虑到{_birth_journey_join_concern_labels(user_labels)}，"
        if period_label:
            plan_reason = f"{prefix}{_birth_journey_plan_item_period_reason(period_label, clean_reason)}"
        else:
            plan_reason = f"{prefix}{clean_reason}"
        return {
            "plan_reason": plan_reason,
            "display_reason": plan_reason,
            "reason_type": "personalized",
            "hide_reason": False,
        }
    if period_label:
        plan_reason = _birth_journey_plan_item_period_reason(period_label, clean_reason)
    else:
        plan_reason = clean_reason or "已按目前提供的信息生成这一项。"
    return {
        "plan_reason": plan_reason,
        "display_reason": "",
        "reason_type": "standard",
        "hide_reason": True,
    }


def _birth_journey_reason_hidden(item: dict[str, Any]) -> bool:
    if item.get("hide_reason") is True:
        return True
    return str(item.get("reason_type") or "").strip() == "standard"


def _birth_journey_plan_item_reason_basis(
    context: dict[str, Any],
    based_on: list[str] | None = None,
    *,
    timeframe: str = "",
    title: str = "",
    source_tags: list[str] | None = None,
    max_labels: int = 4,
) -> tuple[list[str], str]:
    period_label = _birth_journey_plan_item_period_label(timeframe, title, based_on, source_tags)
    user_labels = _birth_journey_plan_condition_labels(context, based_on, max_labels=max_labels)
    user_labels = [label for label in user_labels if not re.search(r"你现在孕\s*\d+\s*周", label)]
    return _unique_text_list(user_labels, max_labels), period_label


def _birth_journey_plan_item_period_reason(period_label: str, reason: str) -> str:
    period = str(period_label or "").strip()
    clean_reason = _birth_journey_plan_reason_text(reason)
    if not period:
        return clean_reason
    if period.endswith("检查窗口"):
        return f"{period}里，{clean_reason}"
    if period.endswith("这个阶段"):
        return f"{period}，{clean_reason}"
    return f"{period}阶段，{clean_reason}"


def _birth_journey_plan_item_period_label(
    timeframe: str,
    title: str,
    based_on: list[str] | None = None,
    source_tags: list[str] | None = None,
) -> str:
    period = str(timeframe or "").strip()
    if not period:
        return ""
    if period in {"本周", "今天", "今天或明天", "未来 7 天", "未来 2-4 周", "补齐孕周后生成清单"}:
        return ""
    keys = set(str(item or "") for item in (based_on or []) if str(item or "").strip())
    tags = set(str(item or "") for item in (source_tags or []) if str(item or "").strip())
    text = f"{title} {' '.join(sorted(tags))}".lower()
    checkup_tokens = (
        "检查",
        "产检",
        "筛查",
        "唐筛",
        "无创",
        "羊穿",
        "nt",
        "b超",
        "大排畸",
        "糖耐",
        "ogtt",
        "gbs",
        "复查",
        "胎监",
        "报告",
        "checkup",
        "screen",
        "scan",
        "gtt",
        "growth",
        "position",
    )
    if "checkup_window" in keys or any(token in text for token in checkup_tokens):
        suffix = "这个检查窗口" if re.search(r"孕\s*\d{1,2}(?:-\d{1,2})?\s*周", period) else "这个阶段"
    else:
        suffix = "这个阶段"
    if period.endswith(("阶段", "生产")):
        return period
    return f"{period}{suffix}"


def _birth_journey_plan_condition_labels(
    context: dict[str, Any],
    based_on: list[str] | None = None,
    *,
    max_labels: int = 4,
) -> list[str]:
    keys = set(str(item or "") for item in (based_on or []) if str(item or "").strip())
    labels: list[str] = []
    week = context.get("current_week")
    if isinstance(week, int) and (not keys or "current_week" in keys or "checkup_window" in keys or "stage_attention" in keys):
        labels.append(f"你现在孕 {week} 周")
    age = _birth_journey_context_age(context)
    if age is not None and age >= 35 and (not keys or "age" in keys):
        labels.append(f"你 {age} 岁属于高龄孕产妇管理范围")
    if _birth_journey_text_is_yes(context.get("ivf")) and (not keys or "ivf" in keys):
        labels.append("你是 IVF/辅助生殖怀孕")
    first_birth = _normalize_first_birth(_first_text(context.get("first_birth")))
    if first_birth == "是" and (not keys or "first_birth" in keys):
        labels.append("这是第一胎")
    elif first_birth == "否" and (not keys or "first_birth" in keys or "prior_birth_history" in keys):
        labels.append("这次不是第一胎")
    fetus_count = _birth_journey_substantive_text(context.get("fetus_count"))
    if fetus_count and (not keys or "fetus_count" in keys or "multiple_pregnancy_type" in keys):
        labels.append(f"你填的是{fetus_count}")
    if _birth_journey_substantive_text(context.get("checkup_status")) and (not keys or "checkup_status" in keys):
        labels.append("你已经提供了产检状态")
    if _birth_journey_has_prior_c_section(context) and (
        not keys or {"previous_birth_method", "previous_c_section_count", "prior_birth_history"} & keys
    ):
        labels.append("你有既往剖宫产相关信息")
    elif _birth_journey_prior_history_text(context) and (not keys or "prior_birth_history" in keys):
        labels.append("你提供了既往孕产经历")
    if _birth_journey_medical_condition_text(context) and (not keys or "medical_notes" in keys):
        labels.append("你填了基础疾病或长期用药")
    if _birth_journey_doctor_note_text(context) and (not keys or "doctor_notes" in keys):
        labels.append("你填了医生特殊提醒")
    if _birth_journey_substantive_text(context.get("risk_factors")) and (not keys or "risk_factors" in keys):
        labels.append("你提到风险因素")
    symptoms = _birth_journey_substantive_text(context.get("current_symptoms"))
    if symptoms and (not keys or "current_symptoms" in keys):
        labels.append("你提到当前有不适或异常变化")
    birth_path = _birth_journey_substantive_text(context.get("birth_path"))
    if birth_path and (not keys or "birth_path" in keys):
        labels.append(f"你计划{birth_path}")
    birth_setting = _birth_journey_substantive_text(context.get("birth_setting"))
    if birth_setting and (not keys or "birth_setting" in keys):
        labels.append(f"生产医院是{birth_setting}")
    city_or_country = _birth_journey_substantive_text(context.get("city_or_country"))
    if city_or_country and (not keys or "city_or_country" in keys or "birth_setting" in keys):
        labels.append(f"你在{city_or_country}")
    support = _birth_journey_support_text(context.get("support_person"))
    if support and (not keys or "support_person" in keys):
        labels.append(f"支持人是{support}")
    lifestyle = _birth_journey_substantive_text(context.get("lifestyle_context"))
    if lifestyle and (not keys or "lifestyle_context" in keys):
        labels.append("你提到生活或工作压力")
    feeding = _birth_journey_substantive_text(context.get("feeding_intention")) or _birth_journey_substantive_text(context.get("feeding_ibclc_context"))
    if feeding and (not keys or "feeding_intention" in keys or "feeding_ibclc_context" in keys):
        labels.append("你提供了喂养准备信息")
    concern_label = _birth_journey_join_concern_labels(_birth_journey_context_concern_labels(context, 2))
    if concern_label and (
        not keys
        or {"entry_reason", "initial_concerns", "entry_concern_followup", "top_worries"} & keys
    ):
        labels.append(f"你提到{concern_label}")
    return _unique_text_list(labels, max_labels)


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
        "没有明显不舒服",
        "没有异常",
        "目前没有异常",
        "未上传产检记录",
        "没有上传产检记录",
        "没有产检记录",
        "跳过产检记录",
        "跳过上传产检记录",
        "暂不上传产检记录",
        "先跳过这步",
        "不清楚先跳过",
        "不确定先跳过",
        "还不确定先跳过",
        "没有高风险因素",
        "有一些风险因素",
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
                _birth_journey_specific_concern_text(context.get("entry_concern_followup")),
                _birth_journey_specific_concern_text(context.get("top_worries")),
                _birth_journey_specific_concern_text(context.get("initial_concerns")),
                _birth_journey_specific_concern_text(context.get("entry_reason")),
                context.get("lifestyle_context"),
            ],
            6,
        )
    )


def _birth_journey_specific_concern_text(value: Any) -> str:
    text = _first_answer_text(value)
    if not text:
        return ""
    return "" if _birth_journey_is_generic_plan_request(text) else text


def _birth_journey_is_generic_plan_request(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    generic_tokens = (
        "制定孕期计划",
        "生成孕期计划",
        "整理孕期计划",
        "孕期计划",
        "当前孕期计划",
        "饮食和运动建议",
        "饮食运动建议",
        "饮食和运动",
        "饮食建议",
        "运动建议",
    )
    third_person_tokens = ("用户希望", "用户想", "用户需要", "用户咨询", "用户询问", "用户要求")
    specific_tokens = (
        "焦虑",
        "无助",
        "迷茫",
        "心里没底",
        "不知道",
        "怎么办",
        "先做什么",
        "怕",
        "担心",
        "担忧",
        "漏事",
        "压力",
        "血压",
        "血糖",
        "胎动",
        "出血",
        "腹痛",
    )
    if any(token in clean for token in specific_tokens):
        return False
    return any(token in clean for token in generic_tokens) or any(token in clean for token in third_person_tokens)


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


BIRTH_JOURNEY_BABY_SIZE_REFERENCES: tuple[tuple[range, str], ...] = (
    (range(1, 2), "草莓籽"),
    (range(2, 4), "芝麻粒"),
    (range(4, 5), "扁豆"),
    (range(5, 6), "蓝莓"),
    (range(6, 7), "芸豆"),
    (range(7, 8), "葡萄"),
    (range(8, 11), "金桔"),
    (range(11, 13), "无花果"),
    (range(13, 15), "小苹果"),
    (range(15, 18), "牛油果"),
    (range(18, 19), "番茄"),
    (range(19, 21), "小圆萝卜"),
    (range(21, 23), "小甜瓜"),
    (range(23, 25), "紫甘蓝"),
    (range(25, 28), "花菜"),
    (range(28, 30), "卷心菜"),
    (range(30, 32), "椰子"),
    (range(32, 34), "金丝瓜"),
    (range(34, 37), "白兰瓜"),
    (range(37, 39), "小西瓜"),
    (range(39, 40), "小南瓜"),
    (range(40, 41), "快出生啦"),
)


def _birth_journey_week_guide(week: Any) -> dict[str, Any]:
    if not isinstance(week, int):
        return {}
    safe_week = max(1, min(40, week))
    return {
        "week": safe_week,
        "baby_size_reference": _birth_journey_baby_size_reference(safe_week),
        "stage_attention": _birth_journey_stage_attention(safe_week),
        "checkup_focus": _birth_journey_checkup_focus_sentence(safe_week),
    }


def _birth_journey_baby_size_reference(week: int) -> str:
    for week_range, label in BIRTH_JOURNEY_BABY_SIZE_REFERENCES:
        if week in week_range:
            return label
    return ""


def _birth_journey_stage_attention(week: int) -> str:
    if week <= 14:
        return "孕早期常见孕吐、反酸、乏力、嗜睡、口水多或尿频，计划会把补剂、饮食和首次产检先排清楚。"
    if week <= 27:
        return "孕中期可能出现乳房胀痛、便秘或睡眠变差，计划会把运动、加餐、补铁补钙和产检窗口拆开安排。"
    return "孕晚期可能出现腰痛、耻骨疼、假性宫缩或妊娠纹，计划会优先放入胎动观察、体重管理和待产准备。"


def _birth_journey_checkup_focus_sentence(week: int) -> str:
    if week <= 5:
        return "先确认验孕结果、末次月经和首次就诊入口，暂时不用把生产准备提前压上来。"
    if week <= 8:
        return "重点确认血 hCG、B 超、宫内妊娠、胎心胎芽和什么时候看报告。"
    if week <= 10:
        return "适合开始整理建档材料、既往检查、用药补剂和下次产检要问的问题。"
    if week <= 14:
        return "建档、NT、早孕筛查和报告回看是主线，检查时间通常不要拖到窗口外。"
    if week <= 17:
        return "如果早期筛查没有完成，要问清中期唐筛、无创 DNA 或羊水穿刺是否需要接上。"
    if week <= 19:
        return "开始把大排畸预约、检查地点、陪同规则和复查方式提前锁定。"
    if week <= 24:
        return "大排畸是这几周重点，要确认当天流程、报告回看和是否需要复查。"
    if week <= 28:
        return "糖耐和 24-28 周产检是重点，要提前排好禁食、抽血、进食和返程。"
    if week <= 31:
        return "进入晚孕期后，要把胎动、血压、体重、水肿和胎儿生长观察固定下来。"
    if week <= 34:
        return "这几周要把胎位、宝宝生长、医院入院方式和待产准备逐步确认好。"
    if week <= 36:
        return "要问清 GBS 筛查、胎位、生长评估、待产包证件和入院入口。"
    return "36 周后通常进入更密集产检，要把胎动、宫缩、破水、见红和入院联系步骤放在最前面。"


def _clean_birth_journey_fragment(value: Any) -> str:
    return str(value or "").strip()


def _birth_journey_week_basis_detail(week: int) -> str:
    guide = _birth_journey_week_guide(week)
    focus = _clean_birth_journey_fragment(guide.get("checkup_focus"))
    return f"你现在孕 {week} 周，计划先按本周产检窗口和身体变化来排：{focus}"


def _birth_journey_safety_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    symptoms_text = "、".join(_text_list(context.get("current_symptoms")))
    if not symptoms_text:
        return []
    items: list[dict[str, Any]] = []
    if any(token in symptoms_text for token in ("出血", "流血", "流水", "破水", "胎动", "腹痛", "头痛", "视物", "发热", "胸痛", "气短", "瘙痒", "皮肤痒", "胆汁酸")):
        items.append(
            _birth_journey_plan_item(
                "做胎动与风险观察",
                "这类变化需要先按医院口径判断，并设置清楚风险触发条件。",
                "现在",
                ["current_symptoms"],
                item_id="safety_current_symptoms",
                priority_type=BIRTH_JOURNEY_PRIORITY_ESSENTIAL,
                source_tags=["current_symptoms", "safety"],
                context=context,
                steps=[
                    "固定时间观察胎动变化",
                    "记录异常症状变化趋势",
                    "保存急诊联系入口",
                    "设置风险触发条件",
                    "出现异常及时联系医院",
                ],
                done_criteria="已按医院或医生口径确认是否需要当天处理，并保存后续观察要求。",
                after_done_value="做完后，再继续排普通孕期准备，会更安心也更不容易误判。",
            )
        )
    return items


def _birth_journey_lifestyle_next_7_item(
    lifestyle_text: str,
    timeframe: str = "从今天开始",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if any(token in lifestyle_text for token in ("通勤", "路上", "坐车", "开车")):
        return _birth_journey_plan_item(
            "做久坐/久站/疲劳管理",
            "把通勤、久坐、久站或疲劳拆成固定休息提醒，避免临时硬撑。",
            timeframe,
            ["lifestyle_context"],
            item_id="condition_lifestyle_commute",
            priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
            source_tags=["lifestyle_context", "commute"],
            context=context,
            steps=[
                "设置定时休息与起身提醒",
                "每45-60分钟活动2-3分钟",
                "久站后及时坐下抬腿休息",
                "记录疲劳与水肿情况",
                "出现异常症状及时就医",
            ],
            done_criteria="已设好本周至少 3 天的通勤后休息提醒。",
            after_done_value="做完后，通勤后的恢复时间会固定下来，不再挤掉休息和产检准备。",
        )
    if any(token in lifestyle_text for token in ("久坐", "坐着", "上班", "办公")):
        return _birth_journey_plan_item(
            "做久坐/久站/疲劳管理",
            "把通勤、久坐、久站或疲劳拆成固定休息提醒，避免临时硬撑。",
            timeframe,
            ["lifestyle_context"],
            item_id="condition_lifestyle_sitting",
            priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
            source_tags=["lifestyle_context", "sitting"],
            context=context,
            steps=[
                "设置定时休息与起身提醒",
                "每45-60分钟活动2-3分钟",
                "久站后及时坐下抬腿休息",
                "记录疲劳与水肿情况",
                "出现异常症状及时就医",
            ],
            done_criteria="已设置提醒，并试运行至少 1 个工作日。",
            after_done_value="做完后，久坐中断会变成日常节奏，也更容易判断不适是否需要问医生。",
        )
    if any(token in lifestyle_text for token in ("睡眠", "失眠", "熬夜", "夜醒", "睡不好")):
        return _birth_journey_plan_item(
            "做久坐/久站/疲劳管理",
            "把睡眠不足和疲劳拆成固定休息提醒，避免一直累积到影响执行。",
            timeframe,
            ["lifestyle_context"],
            item_id="condition_lifestyle_sleep",
            priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
            source_tags=["lifestyle_context", "sleep"],
            context=context,
            steps=[
                "设置定时休息与起身提醒",
                "每45-60分钟活动2-3分钟",
                "久站后及时坐下抬腿休息",
                "记录疲劳与水肿情况",
                "出现异常症状及时就医",
            ],
            done_criteria="已完成一次睡前降噪，并观察睡眠变化。",
            after_done_value="做完后，你能判断哪些安排真的影响睡眠，后续计划会更好调。",
        )
    if any(token in lifestyle_text for token in ("久站", "站着", "站立")):
        return _birth_journey_plan_item(
            "做久坐/久站/疲劳管理",
            "把通勤、久坐、久站或疲劳拆成固定休息提醒，避免临时硬撑。",
            timeframe,
            ["lifestyle_context"],
            item_id="condition_lifestyle_standing",
            priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
            source_tags=["lifestyle_context", "standing"],
            context=context,
            steps=[
                "设置定时休息与起身提醒",
                "每45-60分钟活动2-3分钟",
                "久站后及时坐下抬腿休息",
                "记录疲劳与水肿情况",
                "出现异常症状及时就医",
            ],
            done_criteria="已给本周最容易久站的时段安排休息点或替换人。",
            after_done_value="做完后，你不用临时硬撑，也更容易判断身体不适是否需要问医生。",
        )
    return _birth_journey_plan_item(
        "做久坐/久站/疲劳管理",
        "把生活压力和疲劳先拆成固定休息提醒，避免计划停在担心里。",
        timeframe,
        ["lifestyle_context"],
        item_id="condition_lifestyle_pressure",
        priority_type=BIRTH_JOURNEY_PRIORITY_SUPPORTIVE,
        source_tags=["lifestyle_context"],
        context=context,
        steps=[
            "设置定时休息与起身提醒",
            "每45-60分钟活动2-3分钟",
            "久站后及时坐下抬腿休息",
            "记录疲劳与水肿情况",
            "出现异常症状及时就医",
        ],
        done_criteria="已安排并完成或预约一个 15 分钟减压动作。",
        after_done_value="做完后，计划不会停留在担心里，会变成今天能推进的一小步。",
    )


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
    multiple_type = _birth_journey_substantive_text(context.get("multiple_pregnancy_type"))
    birth_path = str(context.get("birth_path") or "")
    feeding = str(context.get("feeding_intention") or "")
    birth_setting = str(context.get("birth_setting") or "")
    support = str(context.get("support_person") or "")
    medical_notes = _birth_journey_medical_condition_text(context)
    doctor_notes = _birth_journey_doctor_note_text(context)

    if first_birth == "是" and phase_id in {"late_pregnancy", "labor_recognition"}:
        phase["actions"].append("你是第一胎，可以提前让支持人也看一遍入院流程和临产信号，避免到时只靠你一个人判断。")
    if first_birth == "否" and phase_id == "late_pregnancy":
        phase["actions"].append("提前安排大宝接送、陪伴和夜间照护，临产时不要临时找人。")
    if any(token in fetus_count for token in ("双", "多", "三")) and phase_id == "late_pregnancy":
        phase["watchouts"].append(
            f"你填到{multiple_type}，产检和入院节奏更要按医生给出的安排来，别用单胎时间表硬套。"
            if multiple_type and "不适用" not in multiple_type
            else "你是多胎，产检和入院节奏更要按医生给出的安排来，别用单胎时间表硬套。"
        )
    if _birth_journey_has_prior_c_section(context) and phase_id in {"late_pregnancy", "labor_recognition", "hospital_birth"}:
        phase["actions"].append("既往剖宫产信息要提前带给医生确认，重点问这次分娩方式评估和入院时机。")
    if "剖" in birth_path and phase_id in {"late_pregnancy", "hospital_birth", "postpartum"}:
        phase["actions"].append("你是剖宫产，提前问清术前禁食、入院时间、住院天数和术后下床/伤口护理口径。")
    if any(token in feeding for token in ("母乳", "混合", "纯泵")) and phase_id in {"hospital_birth", "postpartum"}:
        phase["actions"].append("你希望母乳或混合喂养，入院后可以尽早确认含乳、涨奶处理和 IBCLC/泌乳顾问支持。")
    if birth_setting and phase_id == "late_pregnancy":
        phase["actions"].append(f"围绕{birth_setting}确认预登记、陪产、探视、夜间入口和停车/打车规则。")
    if support and phase_id in {"late_pregnancy", "postpartum"}:
        phase["actions"].append(f"把{support}要负责的事提前写下来：出发、联系医院、记录、夜间照护和补给。")
    if doctor_notes and phase_id in {"mid_pregnancy", "late_pregnancy"}:
        phase["watchouts"].append("医生已经提醒过的特殊情况要以医院方案为准，产检时把后续观察和入院时机问清楚。")
    if medical_notes and phase_id in {"mid_pregnancy", "late_pregnancy", "hospital_birth"}:
        phase["actions"].append("基础疾病或长期用药信息要和产科、相关专科同步确认，避免复查和用药口径分散。")


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
        data, _ = json.JSONDecoder().raw_decode(raw_json)
    except (TypeError, json.JSONDecodeError):
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
    multiple_pregnancy_type = _first_text(source.get("multiple_pregnancy_type"), source.get("birth_prep_multiple_pregnancy_type"))
    previous_birth_method = _first_text(source.get("previous_birth_method"), source.get("birth_prep_previous_birth_method"))
    previous_c_section_count = _first_text(
        source.get("previous_c_section_count"),
        source.get("birth_prep_previous_c_section_count"),
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
    prior_birth_history = _birth_journey_substantive_text(
        _first_text(source.get("prior_birth_history"), source.get("birth_prep_prior_birth_history"))
    )
    medical_notes = _birth_journey_substantive_text(
        _first_text(
            source.get("medical_notes"),
            source.get("birth_prep_medical_notes"),
            source.get("chronic_conditions"),
            source.get("long_term_medication"),
        )
    )
    doctor_notes = _birth_journey_substantive_text(
        _first_text(
            source.get("doctor_notes"),
            source.get("birth_prep_doctor_notes"),
            source.get("special_notes"),
            source.get("abnormal_results"),
        )
    )
    pregnancy_history_or_notes = "；".join(
        _unique_text_list(
            [
                source.get("pregnancy_history_or_notes"),
                source.get("birth_prep_pregnancy_history_or_notes"),
                prior_birth_history,
                medical_notes,
                doctor_notes,
                source.get("risk_factors"),
                source.get("high_risk_factors"),
            ],
            6,
        )
    )
    if not pregnancy_history_or_notes:
        pregnancy_history_or_notes = _first_text(
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
    if _has_meaningful_value(multiple_pregnancy_type):
        defaults["multiple_pregnancy_type"] = multiple_pregnancy_type
    if _has_meaningful_value(city_or_country):
        defaults["city_or_country"] = city_or_country
    if _has_meaningful_value(birth_hospital):
        defaults["birth_hospital"] = birth_hospital
        defaults["birth_setting"] = birth_hospital
    if birth_path:
        defaults["birth_path"] = birth_path
    if _has_meaningful_value(first_birth):
        defaults["first_birth"] = first_birth
    if _has_meaningful_value(previous_birth_method):
        defaults["previous_birth_method"] = previous_birth_method
    if _has_meaningful_value(previous_c_section_count):
        defaults["previous_c_section_count"] = previous_c_section_count
    if _has_meaningful_value(prior_birth_history):
        defaults["prior_birth_history"] = prior_birth_history
    if _has_meaningful_value(feeding_intention):
        defaults["feeding_intention"] = feeding_intention
    if _has_meaningful_value(return_to_work_timing):
        defaults["return_to_work_timing"] = return_to_work_timing
    if _has_meaningful_value(support_person):
        defaults["support_person"] = support_person
    if _has_meaningful_value(pregnancy_history_or_notes):
        defaults["pregnancy_history_or_notes"] = pregnancy_history_or_notes
    if _has_meaningful_value(medical_notes):
        defaults["medical_notes"] = medical_notes
    if _has_meaningful_value(doctor_notes):
        defaults["doctor_notes"] = doctor_notes
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
