from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from ..types import RuntimeInputs

HOSPITAL_BAG_CART_URL = "/hospital-bag-cart"
HOSPITAL_BAG_CART_LINK = f"[打开待产包一键打包下单页]({HOSPITAL_BAG_CART_URL})"
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
        "你的待产包已经设计好了哦～我顺手把清单里适合直接购买的妈妈/宝宝用品整理到了购物车，"
        "方便直接下单购买，不用一次买完，先按医院会提供什么、家里有什么，删一删再下单就好。\n\n"
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
    (("分娩沟通卡",), "记录生产偏好和需要提前沟通的事，入院时方便给医护看。"),
]
BIRTH_PLAN_DISCLAIMER = (
    "这张卡只用于沟通。请优先遵循医生和医院建议，尤其是因安全原因需要调整计划时。"
)
BIRTH_PLAN_ASSISTANT_FOLLOWUP = {
    "kind": "birth_plan_card_guidance",
    "message": "你可以提前和医院确认，并在产检或入院前把这张卡给医生/护士看，用它快速沟通你的重点偏好和需要讨论的问题。",
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
PLACEHOLDER_VALUES = {"", "to confirm", "待确认", "未确定", "不确定", "还没确定", "还没想好", "none", "n/a"}
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
        "options": ["是", "否", "不确定"],
    },
    {
        "id": "age",
        "label": "基本信息｜妈妈年龄",
        "type": "text",
        "required": False,
        "placeholder": "例如：32；不想填可以留空",
    },
    {
        "id": "bmi_or_weight_context",
        "label": "基本信息｜BMI 或身高体重情况",
        "type": "text",
        "required": False,
        "placeholder": "例如：BMI 24，或身高体重；不确定可留空",
    },
    {
        "id": "pregnancy_history_or_notes",
        "label": "基本信息｜妊娠病史或特殊注意事项",
        "type": "textarea",
        "required": False,
        "placeholder": "例如：妊娠糖尿病、过敏史、医生提醒、行动不便等；没有可留空。",
    },
    {
        "id": "birth_path",
        "label": "生产信息｜计划分娩方式",
        "type": "select",
        "required": True,
        "options": ["顺产", "剖宫产", "未确定"],
    },
    {
        "id": "expected_stay",
        "label": "生产信息｜预计住院时长",
        "type": "select",
        "required": False,
        "options": ["不确定", "1 天", "2-3 天", "4 天或以上", "医生/医院建议为准"],
    },
    {
        "id": "support_person",
        "label": "生产信息｜陪产人或支持人情况",
        "type": "select",
        "required": False,
        "options": ["有，且需要准备物品", "有，但不需要准备物品", "暂时没有", "不确定"],
    },
    {
        "id": "birth_setting",
        "label": "医院信息｜医院、地区或生产地点",
        "type": "text",
        "required": False,
        "placeholder": "例如：某某医院、公立医院、私立医院、月子中心配套医院，或暂未确定",
    },
    {
        "id": "hospital_provided_items",
        "label": "医院信息｜已知医院会提供的物品",
        "type": "textarea",
        "required": False,
        "placeholder": "例如：产褥垫、纸尿裤、宝宝衣物、奶瓶、毛巾。不确定可留空。",
    },
    {
        "id": "feeding_intention",
        "label": "偏好信息｜喂养意向",
        "type": "select",
        "required": True,
        "options": ["母乳", "配方", "混合", "未确定"],
    },
]
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
    "birth_path": "计划分娩方式",
    "feeding_intention": "喂养意向",
}
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
    if form_id == "hospital_bag_intake":
        fields = [field for field in fields if field.get("id") not in REMOVED_HOSPITAL_BAG_FORM_FIELD_IDS]
        fields = [_without_field_help_text(field) for field in fields]
        description = ""
    elif form_id == "birth_plan_card_intake":
        fields = [_sanitize_birth_plan_form_field(_without_field_help_text(field)) for field in fields]
        description = ""
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": form_id,
            "title": args.get("title", ""),
            "description": description,
            "submit_label": args.get("submit_label", "确认"),
            "fields": fields,
        },
    }


def create_hospital_bag_form(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    default_values = _dict_value(args.get("default_values"))
    fields: list[dict[str, Any]] = []
    for template in HOSPITAL_BAG_FORM_FIELDS:
        field = dict(template)
        value = _first_text(default_values.get(field["id"]))
        if value and _normalized_placeholder(value) not in PLACEHOLDER_VALUES:
            field["default_value"] = _normalize_hospital_bag_form_value(field["id"], value)
        fields.append(field)
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": "hospital_bag_intake",
            "title": "信息采集",
            "description": "",
            "submit_label": "开始生成",
            "fields": fields,
        },
    }


def create_hospital_bag_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    form_data = _confirmed_form_data(inputs) or _dict_value(args.get("confirmed_form_data"))
    generation_mode = str(args.get("generation_mode") or "standard")
    card_json = _build_hospital_bag_card_json(form_data, generation_mode, inputs)
    _normalize_hospital_bag_scene_groups(card_json["packing_groups"])
    _apply_hospital_bag_item_explanations(card_json["packing_groups"])
    return {
        "tool_name": "ui_card_create",
        "status": "card_created",
        "card": {
            "card_type": "hospital_bag_card",
            "schema_version": "1.0",
            "card_json": card_json,
        },
        "assistant_followup": dict(HOSPITAL_BAG_CART_ASSISTANT_FOLLOWUP),
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
    default_values = _dict_value(args.get("default_values"))
    fields: list[dict[str, Any]] = []
    for template in BIRTH_PLAN_FORM_FIELDS:
        field = _sanitize_birth_plan_form_field(dict(template))
        value = default_values.get(field["id"])
        if field["id"] == "support_person":
            value = _first_text(value, default_values.get("support_people"))
        normalized_value = _normalize_birth_plan_form_value(field["id"], value)
        if _has_meaningful_value(normalized_value):
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


def create_birth_plan_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    form_data = _confirmed_form_data(inputs) or _dict_value(args.get("confirmed_form_data"))
    card_json: dict[str, Any] = {
        "card_type": "birth_plan_card",
        "schema_version": "1.0",
        "title": "分娩沟通卡",
    }
    assistant_followup = _prepare_birth_plan_card(card_json, inputs, form_data)
    return {
        "tool_name": "ui_card_create",
        "status": "card_created",
        "card": {
            "card_type": "birth_plan_card",
            "schema_version": "1.0",
            "card_json": card_json,
        },
        "assistant_followup": assistant_followup,
    }


def _build_hospital_bag_card_json(form_data: dict[str, Any], generation_mode: str, inputs: RuntimeInputs) -> dict[str, Any]:
    due_date_or_week = _first_text(form_data.get("due_date_or_week")) or "待确认"
    birth_path = _normalize_birth_path(_first_text(form_data.get("birth_path"))) or "待确认"
    feeding_intention = _normalize_feeding_intention(_first_text(form_data.get("feeding_intention"))) or "待确认"
    first_birth = _normalize_first_birth(_first_text(form_data.get("first_birth"))) or "待确认"
    birth_setting = _first_text(form_data.get("birth_setting")) or "待确认"
    expected_stay = _first_text(form_data.get("expected_stay")) or "待确认"
    support_person = _first_text(form_data.get("support_person")) or "待确认"
    provided_items = _split_hospital_provided_items(form_data.get("hospital_provided_items"))
    stage = _hospital_bag_stage(due_date_or_week, generation_mode, inputs)
    missing_fields = [
        label
        for field_id, label in HOSPITAL_BAG_MISSING_LABELS.items()
        if _normalized_placeholder(_first_text(form_data.get(field_id))) in PLACEHOLDER_VALUES
    ]
    context: dict[str, Any] = {
        "stage": stage,
        "birth_path": birth_path,
        "feeding_intention": feeding_intention,
        "first_birth": first_birth,
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
    _apply_hospital_bag_item_explanations(filtered_groups)
    return filtered_groups


def _hospital_bag_document_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "身份证件", "priority": "must", "copy_requirement": "原件"},
        {"label": "医保卡/保险卡", "priority": "must", "copy_requirement": "原件"},
        {"label": "产检本/产检资料", "priority": "must", "copy_requirement": "原件"},
        {"label": "检查报告/化验单", "priority": "recommended", "copy_requirement": "按医院要求"},
        {"label": "医院预登记信息", "priority": "confirm_first", "confirm_question": "确认是否已完成医院预登记，以及入院当天需要出示什么。"},
        {"label": "银行卡/手机支付", "priority": "must"},
        {"label": "紧急联系人信息", "priority": "recommended"},
        {"label": "医生/医院联系电话", "priority": "recommended"},
        {
            "label": "分娩沟通卡",
            "priority": "nice_to_have",
            "explain": "记录生产偏好和需要提前沟通的事，入院时方便给医护看。",
        },
        {
            "label": "准生证/户口本",
            "priority": "confirm_first",
            "confirm_question": "确认医院是否要求携带准生证、户口本及复印件。",
        },
    ]


def _hospital_bag_mom_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        {"label": "手机充电线和充电器", "priority": "must", "note": "长充电线更适合病床旁使用。"},
        {"label": "宽松出院衣物", "priority": "must", "quantity": "1套"},
        {"label": "开襟睡衣/哺乳睡衣", "priority": "recommended", "quantity": "1-2套"},
        {"label": "哺乳文胸/舒适内衣", "priority": "recommended", "quantity": "2-3件"},
        {"label": "防滑拖鞋", "priority": "must", "quantity": "1双"},
        {"label": "吸管杯", "priority": "must", "quantity": "1个"},
        {"label": "产褥垫/产妇卫生巾", "priority": "must", "quantity": _postpartum_pad_quantity(context)},
        {"label": "一次性内裤", "priority": "recommended", "quantity": "若干条"},
        {"label": "洗漱用品", "priority": "recommended", "quantity": "旅行装"},
        {"label": "纸巾/湿巾", "priority": "recommended", "quantity": "少量"},
        {"label": "毛巾", "priority": "recommended", "quantity": "1-2条"},
        {"label": "束发用品", "priority": "nice_to_have"},
        {"label": "润唇膏", "priority": "nice_to_have"},
        {"label": "外套/披肩", "priority": "recommended", "quantity": "1件"},
        {
            "label": "胎监带",
            "priority": "confirm_first",
            "confirm_question": "确认医院是否要求自带胎监带，以及需要几条。",
        },
    ]
    if context.get("birth_path") == "剖宫产":
        items.insert(2, {"label": "高腰宽松内裤", "priority": "recommended", "quantity": "若干条", "note": "更不容易压到腹部。"})
        items.insert(3, {"label": "不压腹出院裤/裙", "priority": "recommended", "quantity": "1套"})
        items.append(
            {
                "label": "收腹带",
                "priority": "confirm_first",
                "confirm_question": "剖宫产先确认医生或医院是否建议使用收腹带。",
            }
        )
    return items


def _hospital_bag_baby_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "宝宝出院衣物", "priority": "must", "quantity": "1套"},
        {"label": "备用连体衣", "priority": "recommended", "quantity": "1-2套"},
        {"label": "包被", "priority": "must", "quantity": "1条"},
        {"label": "小毯子", "priority": "nice_to_have", "quantity": "1条"},
        {"label": "纸尿裤", "priority": "confirm_first", "confirm_question": "确认医院是否提供纸尿裤；如果不提供，再问建议数量。"},
        {"label": "湿巾/棉柔巾", "priority": "recommended", "quantity": "1-2包"},
        {"label": "帽子/袜子", "priority": "recommended", "quantity": "各1-2件"},
        {"label": "口水巾/小方巾", "priority": "nice_to_have", "quantity": "2-3条"},
        {"label": "奶瓶", "priority": "confirm_first", "confirm_question": "确认医院是否允许或需要自带奶瓶。"},
        {"label": "安全提篮/安全座椅", "priority": "confirm_first", "confirm_question": "确认出院交通是否需要安全提篮或安全座椅。"},
    ]


def _hospital_bag_support_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    if not _support_person_needs_bag(context):
        return [
            {"label": "远程联系人名单", "priority": "must", "note": "写清楚临产、入院和出院时分别联系谁。"},
            {"label": "去医院交通方案", "priority": "must"},
            {"label": "出院接送安排", "priority": "recommended"},
            {"label": "家中照护安排", "priority": "recommended", "note": "如有大宝、宠物或家务支持，提前定好负责人。"},
            {"label": "紧急备用联系人", "priority": "recommended"},
        ]
    return [
        {"label": "陪产人身份证件", "priority": "must", "copy_requirement": "原件"},
        {"label": "手机充电器", "priority": "must"},
        {"label": "充电宝", "priority": "recommended"},
        {"label": "换洗衣物", "priority": "recommended", "quantity": "1套"},
        {"label": "外套", "priority": "recommended", "quantity": "1件"},
        {"label": "洗漱用品", "priority": "recommended", "quantity": "1套"},
        {"label": "水和零食", "priority": "recommended", "quantity": "按住院天数"},
        {"label": "停车/支付用品", "priority": "recommended"},
        {"label": "记录工具", "priority": "nice_to_have", "note": "用于记医生交代、出生信息和喂养时间。"},
        {"label": "妈妈的沟通偏好", "priority": "nice_to_have", "note": "提前知道哪些事要先问妈妈。"},
    ]


def _hospital_bag_car_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "备用产褥垫/卫生巾", "priority": "recommended", "quantity": "少量"},
        {"label": "纸巾/湿巾", "priority": "recommended", "quantity": "少量"},
        {"label": "水", "priority": "recommended", "quantity": "少量"},
        {"label": "备用衣物", "priority": "nice_to_have", "quantity": "1套"},
        {"label": "医院路线和停车信息", "priority": "recommended"},
        {"label": "塑料袋/收纳袋", "priority": "recommended"},
        {"label": "备用毛巾", "priority": "nice_to_have", "quantity": "1条"},
        {"label": "车内充电线", "priority": "recommended"},
        {"label": "夜间入口信息", "priority": "confirm_first", "confirm_question": "确认夜间急诊或产科入口在哪里。"},
    ]


def _hospital_bag_postpartum_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    feeding = context.get("feeding_intention")
    if feeding == "配方":
        return [
            {"label": "奶瓶", "priority": "confirm_first", "confirm_question": "确认医院是否允许自带奶瓶，或是否由医院提供。"},
            {"label": "配方奶", "priority": "confirm_first", "confirm_question": "确认医院是否允许自带配方奶，以及品牌或规格要求。"},
            {"label": "奶瓶清洁用品", "priority": "recommended", "quantity": "少量"},
            {"label": "奶嘴", "priority": "recommended", "quantity": "少量"},
            {"label": "消毒设备", "priority": "nice_to_have", "note": "按家庭习惯准备，不一定需要提前买大件。"},
            {"label": "喂养记录工具", "priority": "recommended"},
        ]
    items = [
        {"label": "哺乳文胸/哺乳背心", "priority": "recommended", "quantity": "2-3件"},
        {"label": "防溢乳垫", "priority": "recommended", "quantity": "5-10片"},
        {"label": "便携式吸奶器", "priority": "recommended", "quantity": "1台", "note": "母乳或混合喂养时可作为备用，不是必须购买。"},
        {"label": "储奶袋/储奶瓶", "priority": "recommended", "quantity": "少量"},
        {"label": "乳头霜", "priority": "recommended", "quantity": "1支"},
        {"label": "乳盾", "priority": "confirm_first", "confirm_question": "是否需要乳盾，建议先听医院或哺乳顾问建议。"},
        {"label": "哺乳枕", "priority": "nice_to_have"},
        {"label": "小夜灯", "priority": "nice_to_have"},
        {"label": "宝宝尿布台用品", "priority": "recommended"},
        {"label": "喂养记录工具", "priority": "recommended"},
    ]
    return items if feeding in {"母乳", "混合", "待确认", "未确定"} else []


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
    elif context.get("feeding_intention") in {"母乳", "混合"}:
        questions.append("母乳喂养支持：确认医院是否有产后哺乳指导或泌乳顾问资源。")
    if context.get("stage") in {"packing", "immediate"}:
        questions.append("住院时长/出院要求：确认预计住院几天，以及宝宝出院衣物是否有要求。")
    return _dedupe_strings(questions)


def _hospital_bag_focus_items(stage: str, context: dict[str, Any]) -> list[str]:
    base = ["身份证件", "医保卡/保险卡", "产检资料", "手机充电线和充电器", "宝宝出院衣物和包被"]
    if stage in {"packing", "immediate"}:
        base.insert(3, "产褥垫/产妇卫生巾")
        base.insert(4, "一次性内裤")
    if context.get("feeding_intention") in {"母乳", "混合"}:
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
    if context.get("feeding_intention") in {"母乳", "混合"}:
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
    if field_id == "birth_path":
        return _normalize_birth_path(value) or value
    if field_id == "feeding_intention":
        return _normalize_feeding_intention(value) or value
    if field_id == "first_birth":
        return _normalize_first_birth(value) or value
    return value


def _normalize_birth_plan_form_value(field_id: str, value: Any) -> Any:
    if field_id == "birth_path":
        normalized = _normalize_birth_path(_first_text(value))
        if normalized == "未确定":
            return "还没确定"
        return normalized or _first_text(value)
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
    if feeding == "母乳":
        return "母乳喂养"
    if feeding == "混合":
        return "母乳和配方奶都可能"
    if feeding == "配方":
        return "配方奶"
    if feeding == "未确定":
        return "还没想好"
    return value


def _normalize_feeding_intention(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if any(token in text for token in ("混合", "combo", "mixed")):
        return "混合"
    if any(token in text for token in ("配方", "奶粉", "formula")):
        return "配方"
    if any(token in text for token in ("母乳", "亲喂", "breast")):
        return "母乳"
    if text in {"未确定", "不确定", "还没想好", "unknown"}:
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


def _support_person_needs_bag(context: dict[str, Any]) -> bool:
    value = str(context.get("support_person") or "")
    return not any(token in value for token in ("暂时没有", "不需要", "没有"))


def _postpartum_pad_quantity(context: dict[str, Any]) -> str:
    if context.get("birth_path") == "剖宫产" or str(context.get("expected_stay")) in {"4 天或以上"}:
        return "20片左右"
    return "10-20片"


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


def create_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    card_json = args.get("card_json", {})
    if not isinstance(card_json, dict):
        card_json = {}
    card_type = args.get("card_type", "")
    assistant_followup = None
    if card_type == "hospital_bag_card":
        assistant_followup = _prepare_hospital_bag_card(card_json, inputs)
    elif card_type == "birth_plan_card":
        assistant_followup = _prepare_birth_plan_card(card_json, inputs)

    result = {
        "tool_name": "ui_card_create",
        "status": "card_created",
        "card": {
            "card_type": card_type,
            "schema_version": args.get("schema_version", ""),
            "card_json": card_json,
        },
    }
    if assistant_followup:
        result["assistant_followup"] = assistant_followup
    return result


def _prepare_hospital_bag_card(card_json: dict[str, Any], inputs: RuntimeInputs) -> dict[str, str] | None:
    title = str(card_json.get("title") or "").strip()
    if not title or title in {"待产包卡片", "Hospital Bag Card"}:
        card_json["title"] = "待产包"
    elif "待产包卡片" in title:
        card_json["title"] = title.replace("待产包卡片", "待产包")

    groups = card_json.get("packing_groups")
    if not isinstance(groups, list):
        groups = []
        card_json["packing_groups"] = groups
    _normalize_hospital_bag_scene_groups(groups)
    _apply_hospital_bag_item_explanations(groups)

    if _formula_only_feeding_intention(inputs):
        return dict(HOSPITAL_BAG_CART_ASSISTANT_FOLLOWUP)

    group = _find_lactation_or_postpartum_group(groups)
    if group is None:
        group = {"group_id": "postpartum_home_first_week", "title": "产后回家第一周用品", "items": []}
        groups.append(group)

    items = group.get("items")
    if not isinstance(items, list):
        items = []
        group["items"] = items

    _ensure_breast_pump_visible(items)
    _apply_hospital_bag_item_explanations(groups)

    return dict(HOSPITAL_BAG_CART_ASSISTANT_FOLLOWUP)


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
        "title": _localized_birth_plan_title(_first_text(source.get("title"))) or "分娩沟通卡",
        "subtitle": _localized_birth_plan_subtitle(_first_text(source.get("subtitle"))) or "产房沟通优先级卡片",
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
        feeding_intention = str(data.get("feeding_intention", "")).strip().lower()
        return feeding_intention in {"配方", "formula", "formula feeding"}
    message = str(inputs.get("user_message", ""))
    marker = "confirmed_form_data:"
    if marker not in message:
        return False
    raw_json = message.split(marker, 1)[1].strip()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return False
    feeding_intention = str(data.get("feeding_intention", "")).strip().lower()
    return feeding_intention in {"配方", "formula", "formula feeding"}


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


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
        return "分娩沟通卡"
    return value


def _localized_birth_plan_subtitle(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"birth plan card", "labor room communication priority card"}:
        return "产房沟通优先级卡片"
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
        notes.append(f"基于{'、'.join(parts)}，整理成适合产检或入院沟通的重点卡片。")
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
        for optional_key in ("help_text", "placeholder", "default_value"):
            value = field.get(optional_key)
            if value is not None and value != "":
                normalized[optional_key] = value
        options = field.get("options")
        if isinstance(options, list) and options:
            normalized["options"] = [str(option) for option in options]
        if normalized["id"] and normalized["label"]:
            fields.append(normalized)

    return fields
