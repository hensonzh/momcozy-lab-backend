from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


HOSPITAL_BAG_CART_URL = "/hospital-bag-cart"
HOSPITAL_BAG_CART_LINK = f"[打开待产包购物车]({HOSPITAL_BAG_CART_URL})"
HOSPITAL_BAG_DISCLAIMER = "请优先遵循医院要求和医生/助产士的具体指导。"
HOSPITAL_BAG_CART_USD_TO_CNY_RATE = 6.8
MOMCOZY_PUMP_OFFICIAL_COLLECTION_URL = "https://momcozy.com/collections/wearable-breast-pump"
MOMCOZY_PUMP_OFFICIAL_OVERVIEW_URL = "https://momcozy.com/collections/electric-breast-pump"
MOMCOZY_PUMP_SUPPORT_GUIDE_URL = "https://support.momcozy.com/article/56837165211801"

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

HOSPITAL_BAG_FORM_FIELDS: list[dict[str, Any]] = [
    {
        "id": "due_date_or_week",
        "label": "基本信息｜预产期或当前孕周",
        "type": "text",
        "required": True,
        "placeholder": "例如：2026-06-12 或 37 周",
    },
    {"id": "first_birth", "label": "基本信息｜是否第一胎", "type": "select", "required": True, "options": ["是", "否"]},
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
        "options": ["没有", "妊娠糖尿病", "血压或子痫前期风险", "胎盘问题", "早产风险", "宝宝可能 NICU", "其它"],
    },
    {"id": "birth_path", "label": "生产信息｜分娩方式", "type": "select", "required": True, "options": ["顺产", "剖宫产", "还不确定"]},
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
        "options": ["不知道什么时候去医院", "怕漏买", "怕母乳不够", "怕剖宫产恢复", "怕产后没人帮", "怕宝宝用品准备不全", "其它"],
    },
]

HOSPITAL_BAG_REQUIRED_LABELS = {
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

BIRTH_PLAN_FORM_FIELDS: list[dict[str, Any]] = [
    {
        "id": "due_date_or_week",
        "label": "基本信息｜现在怀孕多久/预产期",
        "type": "text",
        "required": True,
        "placeholder": "例如：2026-06-12 或 37 周",
    },
    {"id": "birth_path", "label": "基本信息｜医生目前建议的生产方式", "type": "select", "required": True, "options": ["顺产", "剖宫产", "还没确定"]},
    {
        "id": "birth_setting",
        "label": "基本信息｜准备在哪家医院/哪里生",
        "type": "text",
        "required": False,
        "placeholder": "例如：某某医院、助产中心，或暂未确定",
    },
    {"id": "first_birth", "label": "基本信息｜是不是第一胎", "type": "select", "required": False, "options": ["是", "否", "还没确定"]},
    {
        "id": "top_priorities",
        "label": "支持与沟通｜最希望医护知道的事",
        "type": "multi_select",
        "required": True,
        "options": ["宝宝出生后，想尽早抱一抱/贴一贴", "想尽早试着喂母乳", "希望伴侣/支持人尽量陪在身边", "希望医护多鼓励我、告诉我进展", "一些非必要操作，希望先和我沟通"],
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
        "options": ["做操作前，先告诉我为什么需要", "做重要决定前，先问问我的想法", "重要决定也请同步伴侣/支持人", "请用简单清楚的话说明", "计划有变化时，请先说原因和选择", "需要翻译或语言支持"],
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
        "options": ["医生允许时，希望可以走动或换姿势", "宝宝心跳监护怎么做，希望先说明一下", "希望可以用分娩球、热敷或按摩让自己舒服一点", "想提前确认生产时能不能喝水或吃点东西", "希望环境安静一点、灯光柔和一点"],
    },
    {
        "id": "intervention_preferences",
        "label": "生产过程｜需要先说清楚的操作",
        "type": "multi_select",
        "required": False,
        "options": ["如果需要侧切，请先说明原因再和我沟通", "如果需要产钳或吸引，请先解释为什么需要", "如果需要人工破水，请先和我说明", "灌肠或剃毛前，希望先告诉我是否必须"],
    },
    {
        "id": "pain_relief_preferences",
        "label": "疼痛和舒适｜生产时怎么帮你舒服一点",
        "type": "multi_select",
        "required": False,
        "options": ["想提前了解有哪些减痛/麻醉选择", "如果安全允许，先试试呼吸、姿势、按摩来缓解", "我倾向使用无痛/硬膜外，想提前沟通安排", "有点担心副作用或恢复，想先了解清楚再决定", "如果剖宫产，希望手术麻醉前充分说明"],
    },
    {
        "id": "pain_relief_notes",
        "label": "疼痛和舒适｜其他关于疼痛缓解/麻醉的想法",
        "type": "textarea",
        "required": False,
        "placeholder": "如果上面的选项没覆盖，可以简单写一句；不确定可留空。",
    },
    {"id": "feeding_intention", "label": "宝宝出生后｜准备怎么喂宝宝", "type": "select", "required": False, "options": ["母乳喂养", "母乳和配方奶都可能", "配方奶", "还没想好"]},
    {
        "id": "baby_after_birth_preferences",
        "label": "宝宝出生后｜宝宝出生后希望怎么安排",
        "type": "multi_select",
        "required": False,
        "options": ["宝宝出生后，想尽早抱一抱/贴一贴", "想尽早试着亲喂/喂母乳", "如果医院允许，希望晚一点剪脐带", "希望宝宝尽量和我在一起", "给宝宝做检查或护理前，希望先告诉我", "打针、疫苗或新生儿检查前，希望先说明", "如果医院允许，希望伴侣/家人剪脐带", "如果医院允许，第一次洗澡晚一点", "如果宝宝需要离开我身边，请说明原因和大概多久", "给宝宝用配方奶或奶瓶前，请先和我沟通"],
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
        "options": ["陪产和探视怎么安排", "能不能拍照或录像", "生产时能不能喝水或吃点东西", "无痛或麻醉什么时候可以沟通", "宝宝出生后的护理流程", "产后有没有母乳喂养支持", "大概住几天、怎么出院", "紧急情况会怎么沟通和决定"],
    },
    {
        "id": "medical_notes",
        "label": "提前问医院｜过敏、医生提醒或其他安全信息",
        "type": "textarea",
        "required": False,
        "placeholder": "只填写你明确知道的信息，例如：过敏、医生已说明的限制、医院要求。不确定可留空。",
    },
]

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
    replacement["id"]: original_id for original_id, replacement in HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS.items()
}


def create_legacy_artifact_result(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "hospital_bag_form_create":
        return hospital_bag_form_result(args)
    if tool_name == "birth_plan_form_create":
        return birth_plan_form_result(args)
    if tool_name == "hospital_bag_card_create":
        return hospital_bag_card_result(args)
    if tool_name == "labor_communication_card_create":
        return labor_communication_card_result(args)
    if tool_name == "birth_journey_plan_card_create":
        return birth_journey_plan_card_result(args)
    if tool_name == "hospital_bag_cart_update":
        return hospital_bag_cart_update_result(args)
    if tool_name == "hospital_bag_pump_recommend":
        return hospital_bag_pump_recommend_result(args)
    raise ValueError(f"Unsupported legacy artifact tool: {tool_name}")


def artifact_record_from_legacy_result(result: dict[str, Any]) -> dict[str, Any] | None:
    status = _text(result.get("status"))
    if status not in {"form_created", "card_created", "existing_plan_found", "cart_updated", "cart_unchanged"}:
        return None
    form = _dict(result.get("form"))
    if form:
        return {
            "artifact_type": "form",
            "schema_version": "1.0",
            "payload": {"tool_name": result.get("tool_name"), "form": form},
        }
    card = _dict(result.get("card"))
    if card:
        card_json = _dict(card.get("card_json"))
        return {
            "artifact_type": _text(card.get("card_type")) or _text(card_json.get("card_type")) or "card",
            "schema_version": _text(card.get("schema_version")) or _text(card_json.get("schema_version")) or "1.0",
            "payload": {
                "tool_name": result.get("tool_name"),
                "card": card,
                "card_json": card_json,
                "assistant_followup": result.get("assistant_followup"),
            },
        }
    cart_update = _dict(result.get("cart_update"))
    if cart_update:
        return {
            "artifact_type": "hospital_bag_cart",
            "schema_version": "1.0",
            "payload": {"tool_name": result.get("tool_name"), "cart_update": cart_update, "summary": result.get("summary")},
        }
    return None


def hospital_bag_form_result(args: dict[str, Any]) -> dict[str, Any]:
    default_values = _dict(args.get("default_values"))
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": "hospital_bag_intake",
            "title": "信息采集",
            "description": "",
            "submit_label": "提交",
            "fields": _fields_with_defaults(HOSPITAL_BAG_FORM_FIELDS, default_values),
            "default_values": _allowed_defaults(HOSPITAL_BAG_FORM_FIELDS, default_values),
        },
    }


def birth_plan_form_result(args: dict[str, Any]) -> dict[str, Any]:
    default_values = _dict(args.get("default_values"))
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": "birth_plan_card_intake",
            "title": "信息采集",
            "description": "",
            "submit_label": "生成我的沟通卡",
            "fields": _fields_with_defaults(BIRTH_PLAN_FORM_FIELDS, default_values),
        },
    }


def hospital_bag_card_result(args: dict[str, Any]) -> dict[str, Any]:
    form_data = _confirmed_form_data(args)
    if not form_data:
        return _needs_context_result(
            "hospital_bag_card_create",
            "needs_confirmed_form_data",
            "生成待产包清单前，需要先提交待产包信息采集表单。",
            list(HOSPITAL_BAG_REQUIRED_LABELS),
            "请先完成并提交待产包信息采集表单，我再根据确认后的信息整理待产包清单。",
        )
    missing = [field_id for field_id in HOSPITAL_BAG_REQUIRED_LABELS if not _has_value(form_data.get(field_id))]
    if missing:
        labels = [HOSPITAL_BAG_REQUIRED_LABELS[field_id] for field_id in missing[:3]]
        return _needs_context_result(
            "hospital_bag_card_create",
            "needs_required_form_fields",
            "生成待产包清单前，需要先补全待产包表单必填信息。",
            missing,
            f"待产包清单还不能生成，表单里还差{'、'.join(labels)}。请先补全并提交待产包信息采集表单。",
        )
    card_json = _hospital_bag_card_json(form_data)
    return {
        "tool_name": "hospital_bag_card_create",
        "status": "card_created",
        "card": {"card_type": "hospital_bag_card", "schema_version": "1.0", "card_json": card_json},
        "assistant_followup": {
            "kind": "hospital_bag_cart",
            "message": (
                "待产包清单我整理好了。\n\n"
                "我顺手把清单里适合放入购物车参考的妈妈/宝宝用品整理好了，"
                "不用一次买完，先看清单里的优先级，按实际情况删减后再决定是否购买。\n\n"
                f"**{HOSPITAL_BAG_CART_LINK}**"
            ),
        },
    }


def labor_communication_card_result(args: dict[str, Any]) -> dict[str, Any]:
    form_data = _confirmed_form_data(args)
    if not form_data:
        return _needs_context_result(
            "labor_communication_card_create",
            "needs_confirmed_form_data",
            "生成分娩沟通单前，需要先提交分娩沟通单信息采集表单。",
            ["confirmed_form_data"],
            "请先完成并提交分娩沟通单信息采集表单，我再根据确认后的信息整理沟通单。",
        )
    card_json = _birth_plan_card_json(form_data)
    return {
        "tool_name": "labor_communication_card_create",
        "status": "card_created",
        "card": {"card_type": "birth_plan_card", "schema_version": "1.0", "card_json": card_json},
        "assistant_followup": "我已经把你的生产偏好整理成沟通单了。可以带着它和医生、助产士或家人一起确认。",
    }


def birth_journey_plan_card_result(args: dict[str, Any]) -> dict[str, Any]:
    plan_context = _dict(args.get("plan_context")) or _confirmed_form_data(args) or _dict(args.get("payload"))
    due = _first_text(plan_context.get("due_date_or_week"), plan_context.get("current_week"), plan_context.get("due_date")) or "待确认"
    card_json = {
        "card_type": "birth_journey_plan_card",
        "schema_version": "1.0",
        "todo_engine_version": "legacy-compatible-1.0",
        "title": "孕期计划",
        "subtitle": "把现在到生产前后要做的事按阶段排清楚",
        "owner": {
            "due_date_or_week": due,
            "birth_path": _first_text(plan_context.get("birth_path"), plan_context.get("delivery_method")),
            "birth_setting": _first_text(plan_context.get("birth_setting"), plan_context.get("birth_hospital"), plan_context.get("hospital")),
            "support_person": _first_text(plan_context.get("support_person"), plan_context.get("support_people")),
            "feeding_intention": _first_text(plan_context.get("feeding_intention"), plan_context.get("feeding_plan")),
        },
        "todo_plan": {
            "periods": [
                {
                    "id": "current_stage",
                    "title": "当前阶段",
                    "subtitle": "先处理最影响安心感的事项",
                    "display_mode": "expanded",
                    "status": "current",
                    "items": [
                        _todo_item("confirm_checkup_questions", "整理下次产检要问的问题", "把最担心的 3-5 个问题写下来，产检时直接确认。"),
                        _todo_item("prepare_hospital_bag", "开始整理待产包", "先准备证件、妈妈住院用品和宝宝出院用品，不用一次买齐。"),
                        _todo_item("confirm_support_plan", "确认产后前两周支持安排", "确认谁陪产、谁接送、谁负责家务和夜间照护。"),
                    ],
                },
                {
                    "id": "labor_and_hospital",
                    "title": "临产与住院",
                    "subtitle": "把去医院和住院沟通提前准备好",
                    "display_mode": "collapsed",
                    "status": "upcoming",
                    "items": [
                        _todo_item("know_labor_signs", "确认需要去医院的信号", "向医院确认破水、出血、胎动减少、规律宫缩时的处理方式。"),
                        _todo_item("birth_plan_card", "整理分娩沟通单", "提前写清楚生产偏好和需要医护先沟通的事项。"),
                    ],
                },
            ]
        },
        "generation_context": {"source": "agent", "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"},
        "next_action": {"label": "继续整理待产包", "send_text": "帮我整理一份个性化待产包清单"},
        "disclaimer": "这份计划用于准备和沟通，不能替代医生、助产士或医院的具体建议；有破水、出血、胎动明显减少、规律宫缩加密或明显不适时，请按医院或医生指导处理。",
    }
    card_json["owner"] = {key: value for key, value in card_json["owner"].items() if _has_value(value)}
    return {
        "tool_name": "birth_journey_plan_card_create",
        "status": "card_created",
        "card": {"card_type": "birth_journey_plan_card", "schema_version": "1.0", "card_json": card_json},
        "plan": {"payload": card_json, "status": "active"},
    }


def hospital_bag_cart_update_result(args: dict[str, Any]) -> dict[str, Any]:
    action = _text(args.get("action"))
    assistant_message = _text(args.get("assistant_message")) or _text(args.get("message")) or _text(args.get("summary"))
    item_ids = _string_list(args.get("item_ids"))
    preserve_item_ids = _string_list(args.get("preserve_item_ids"))
    current_groups = _cart_groups_from_args(args)

    if action in {"replace_pump_model", "add_pump_model"}:
        product = _momcozy_pump_product(_text(args.get("product_sku_id")))
        if product is None:
            message = assistant_message or "你想换成哪一款 Momcozy 吸奶器？比如 S12 Pro Quick、M5 Smart、M9。"
            return _cart_needs_clarification(message)
        next_groups, changed = _upsert_hospital_bag_pump_model(current_groups, product)
        totals = _cart_totals(next_groups)
        if changed["mode"] == "unchanged":
            message = assistant_message or f"购物车里已经是「{product['name']}」了，我先不重复添加。"
        elif changed["mode"] == "added":
            message = assistant_message or f"好，我把「{product['name']}」加到母乳喂养里了，官方价折合约 {_pump_price_cny_label(product)}。"
        else:
            message = assistant_message or f"好，我把「{changed.get('from_name') or '原来的吸奶器'}」换成「{product['name']}」了，官方价折合约 {_pump_price_cny_label(product)}。"
        return _hospital_bag_cart_update_envelope(
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
            budget_mode=_text(args.get("budget_mode")) or "cheaper",
            preference=_text(args.get("preference")) or "balanced",
            preserve_item_ids=preserve_item_ids,
            allow_remove_pump=bool(args.get("allow_remove_pump")),
        )
        message = assistant_message or _hospital_bag_budget_message(budget_result)
        return _hospital_bag_cart_update_envelope(
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
            return _cart_needs_clarification(assistant_message or _missing_item_message(action))
        next_groups, removed_names = _remove_hospital_bag_cart_items(current_groups, item_ids)
        if not removed_names:
            message = assistant_message or "我没有在当前购物车里找到这件商品，你可以再说一下商品名。"
            return _cart_unchanged(message)
        totals = _cart_totals(next_groups)
        names = "、".join(f"「{name}」" for name in removed_names)
        message = assistant_message or _remove_items_message(action, names, totals)
        return _hospital_bag_cart_update_envelope(
            action,
            next_groups,
            totals,
            message,
            removed_item_ids=item_ids,
            removed_item_names=removed_names,
        )

    if action == "restore_items":
        if not item_ids:
            return _cart_needs_clarification(assistant_message or "你想加回哪一件？直接告诉我商品名就行。")
        next_groups, restored_names = _restore_hospital_bag_cart_items(current_groups, item_ids)
        totals = _cart_totals(next_groups)
        if not restored_names:
            message = assistant_message or "这些商品已经在购物车里了，不需要重复添加。"
        else:
            names = "、".join(f"「{name}」" for name in restored_names)
            message = assistant_message or f"好，我把{names}加回购物车了，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_envelope(
            action,
            next_groups,
            totals,
            message,
            restored_item_ids=item_ids,
            restored_item_names=restored_names,
        )

    if action == "replace_items":
        next_groups, replaced_items = _replace_hospital_bag_cart_items(current_groups, item_ids)
        totals = _cart_totals(next_groups)
        if not replaced_items:
            message = assistant_message or "当前购物车里暂时没有可替换成基础款的商品。"
        else:
            names = "、".join(f"「{item['from_name']}」换成「{item['to_name']}」" for item in replaced_items)
            message = assistant_message or f"可以，我先帮你把{names}，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_envelope(action, next_groups, totals, message, replaced_items=replaced_items)

    if action == "update_quantity":
        quantity_updates = args.get("quantity_updates")
        if not isinstance(quantity_updates, list) or not quantity_updates:
            return _cart_needs_clarification(assistant_message or "你想把哪件商品改成几件？直接告诉我商品名和数量就行。")
        next_groups, updated_names, removed_names = _update_hospital_bag_cart_quantities(current_groups, quantity_updates)
        totals = _cart_totals(next_groups)
        if not updated_names and not removed_names:
            message = assistant_message or "我没有在当前购物车里找到要调整的商品，你可以再说一下商品名。"
        else:
            parts: list[str] = []
            if updated_names:
                parts.append("调整了" + "、".join(f"「{name}」" for name in updated_names))
            if removed_names:
                parts.append("移除了" + "、".join(f"「{name}」" for name in removed_names))
            message = assistant_message or f"好，我已经{'，'.join(parts)}，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_envelope(action, next_groups, totals, message, removed_item_names=removed_names)

    if action in {"reset_cart", ""}:
        next_groups = _clone_hospital_bag_cart_groups(DEFAULT_HOSPITAL_BAG_CART_GROUPS)
        totals = _cart_totals(next_groups)
        message = assistant_message or f"已经帮你把待产包购物车恢复到默认清单了，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_envelope("reset_cart", next_groups, totals, message)

    if action == "clarify":
        return _cart_needs_clarification(assistant_message or "你想怎么调整购物车？比如删掉某件、换便宜一点，或者恢复默认清单。")

    return _cart_needs_clarification(assistant_message or "你想怎么调整待产包购物车？")


def hospital_bag_pump_recommend_result(args: dict[str, Any]) -> dict[str, Any]:
    use_case = _text(args.get("use_case")) or "unknown"
    preference = _text(args.get("preference")) or "balanced"
    feeding_intention = _text(args.get("feeding_intention")) or "unknown"
    requested_model = _text(args.get("requested_model"))
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
    message = _pump_recommendation_message(
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
        "summary": message,
        "message": message,
        "recommended_product": _public_pump_product(recommended),
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


def _hospital_bag_card_json(form_data: dict[str, Any]) -> dict[str, Any]:
    birth_path = _first_text(form_data.get("birth_path")) or "待确认"
    feeding = _first_text(form_data.get("feeding_intention")) or "待确认"
    support = _first_text(form_data.get("support_person")) or "待确认"
    return {
        "card_type": "hospital_bag_card",
        "schema_version": "1.0",
        "title": "待产包",
        "subtitle": "个性化入院物品清单",
        "owner": {
            "due_date_or_week": _first_text(form_data.get("due_date_or_week")) or "待确认",
            "birth_setting": _first_text(form_data.get("birth_setting")) or "待确认",
            "birth_path": birth_path,
            "first_birth": _first_text(form_data.get("first_birth")) or "待确认",
            "feeding_intention": feeding,
            "support_person": support,
            "fetus_count": _first_text(form_data.get("fetus_count")) or "待确认",
            "return_to_work_timing": _first_text(form_data.get("return_to_work_timing")) or "待确认",
        },
        "hospital_context": {
            "expected_stay": _first_text(form_data.get("expected_stay")) or "待确认",
            "hospital_provided_items": _string_list(form_data.get("hospital_provided_items")),
            "items_to_confirm_with_hospital": ["医院是否提供纸尿裤/产褥垫", "入院证件和产检资料要求", "陪产、探视和停车安排"],
        },
        "focus_items": ["证件文件包", "妈妈住院包", "宝宝出院包", "母乳喂养备用用品"],
        "hospital_questions": ["需要自带胎监带吗？", "宝宝出生后护理流程是什么？", "产后有没有母乳喂养支持？"],
        "packing_groups": [
            {"group_id": "documents", "title": "证件文件包", "items": [_bag_item("身份证件", "must"), _bag_item("医保卡/保险卡", "must"), _bag_item("产检本/产检资料", "must")]},
            {"group_id": "mom_hospital_bag", "title": "妈妈住院包", "items": [_bag_item("宽松出院衣物", "must", "1套"), _bag_item("产褥垫/产妇卫生巾", "must"), _bag_item("一次性内裤", "recommended")]},
            {"group_id": "baby_discharge_bag", "title": "宝宝出院包", "items": [_bag_item("宝宝出院衣物", "must", "1套"), _bag_item("包被", "must", "1条"), _bag_item("纸尿裤", "confirm_first")]},
            {"group_id": "feeding", "title": "母乳喂养备用", "items": [_bag_item("防溢乳垫", "recommended"), _bag_item("乳头护理霜", "recommended"), _bag_item("便携式吸奶器", "recommended", "1台")]},
        ],
        "missing_or_to_buy": [],
        "timeline": ["32～34 周：确认医院要求", "36 周左右：按清单打包", "临产前：把证件和充电器放到随手可拿处"],
        "personalized_notes": [f"分娩方式：{birth_path}", f"喂养意向：{feeding}", f"支持情况：{support}"],
        "missing_fields": [],
        "disclaimer": HOSPITAL_BAG_DISCLAIMER,
    }


def _birth_plan_card_json(form_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_type": "birth_plan_card",
        "schema_version": "1.0",
        "title": "分娩沟通单",
        "owner": {
            "due_date_or_week": _first_text(form_data.get("due_date_or_week")),
            "birth_path": _first_text(form_data.get("birth_path")),
            "birth_setting": _first_text(form_data.get("birth_setting")),
            "first_birth": _first_text(form_data.get("first_birth")),
        },
        "top_priorities": _string_list(form_data.get("top_priorities")),
        "support_person": _first_text(form_data.get("support_person")),
        "communication_preferences": _string_list(form_data.get("communication_preferences")),
        "labor_preferences": _string_list(form_data.get("labor_preferences")),
        "intervention_preferences": _string_list(form_data.get("intervention_preferences")),
        "pain_relief_preferences": _string_list(form_data.get("pain_relief_preferences")),
        "feeding_intention": _first_text(form_data.get("feeding_intention")),
        "baby_after_birth_preferences": _string_list(form_data.get("baby_after_birth_preferences")),
        "if_plans_change": _first_text(form_data.get("if_plans_change")),
        "emergency_authorization": _first_text(form_data.get("emergency_authorization")),
        "questions_for_hospital": _string_list(form_data.get("hospital_questions_focus")),
        "medical_notes": _string_list(form_data.get("medical_notes")),
        "personalized_notes": _string_list(form_data.get("priority_notes")),
        "disclaimer": "这份沟通单只用于沟通。请优先遵循医生和医院建议，尤其是因安全原因需要调整计划时。",
    }


def _needs_context_result(tool_name: str, status: str, summary: str, missing_fields: list[str], question: str) -> dict[str, Any]:
    return {"tool_name": tool_name, "status": status, "summary": summary, "missing_fields": missing_fields, "data": {"confirmation_question": question}}


def _fields_with_defaults(fields: list[dict[str, Any]], default_values: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for field in fields:
        next_field = deepcopy(field)
        value = default_values.get(str(field.get("id") or ""))
        if _has_value(value):
            next_field["default_value"] = value
        result.append(next_field)
    return result


def _allowed_defaults(fields: list[dict[str, Any]], default_values: dict[str, Any]) -> dict[str, Any]:
    field_ids = {str(field.get("id") or "") for field in fields}
    return {key: value for key, value in default_values.items() if key in field_ids and _has_value(value)}


def _confirmed_form_data(args: dict[str, Any]) -> dict[str, Any]:
    for key in ("confirmed_form_data", "form_data", "payload"):
        value = _dict(args.get(key))
        if value:
            return value
    return {}


def _bag_item(label: str, priority: str, quantity: str = "") -> dict[str, Any]:
    item = {"label": label, "priority": priority}
    if quantity:
        item["quantity"] = quantity
    return item


def _todo_item(item_id: str, title: str, reason: str) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "title": title,
        "status": "pending",
        "plan_reason": reason,
        "priority_type": "essential",
        "priority_label": "必要事项",
    }


def _sanitize_cart_groups(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    groups: list[dict[str, Any]] = []
    for group in value:
        source = _dict(group)
        if not source:
            continue
        items = [_cart_item(item) for item in source.get("items", []) if _dict(item)]
        tone = _text(source.get("tone")) or "rose"
        groups.append({"title": _text(source.get("title")) or "待产包", "tone": tone if tone in {"rose", "mint", "sky"} else "rose", "items": items})
    return groups


def _cart_item(value: Any) -> dict[str, Any]:
    source = _dict(value)
    item = {
        "id": _text(source.get("id")) or _text(source.get("name")) or "item",
        "name": _text(source.get("name")) or _text(source.get("label")) or "待产包用品",
        "desc": _text(source.get("desc")),
        "qty": max(1, int(_number(source.get("qty") or source.get("quantity"), default=1))),
        "price": round(_number(source.get("price"), default=0), 2),
        "keywords": _string_list(source.get("keywords")),
    }
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
        if source.get(key) is not None:
            item[key] = source[key]
    _apply_default_cart_item_image(item)
    return item


def _cart_totals(groups: list[dict[str, Any]]) -> dict[str, Any]:
    item_count = 0
    subtotal = 0.0
    converted_usd_subtotal = 0.0
    for group in groups:
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            qty = max(1, int(_number(item.get("qty"), default=1)))
            item_count += qty
            line_total = _cart_item_line_total_cny(item, qty)
            subtotal = round(subtotal + line_total, 2)
            if _cart_item_currency(item) == "USD":
                converted_usd_subtotal = round(converted_usd_subtotal + line_total, 2)
    subtotal = round(subtotal, 2)
    discount = round(subtotal * 0.08, 2) if item_count > 0 else 0
    shipping = 0
    total = round(subtotal - discount + shipping, 2)
    return {
        "subtotal": subtotal,
        "item_count": item_count,
        "itemCount": item_count,
        "discount": discount,
        "shipping": shipping,
        "total": total,
        "currency": "CNY",
        "currency_totals": [
            {
                "currency": "CNY",
                "subtotal": subtotal,
                "discount": discount,
                "shipping": shipping,
                "total": total,
                "itemCount": item_count,
            }
        ],
        "mixed_currency": False,
        "exchange_rate_usd_cny": HOSPITAL_BAG_CART_USD_TO_CNY_RATE,
        "converted_usd_subtotal": converted_usd_subtotal,
    }


def _cart_groups_from_args(args: dict[str, Any]) -> list[dict[str, Any]]:
    for value in (args.get("groups"), _dict(args.get("cart_update")).get("groups"), _dict(args.get("hospital_bag_cart")).get("groups")):
        groups = _sanitize_cart_groups(value)
        if groups:
            return groups
    return _clone_hospital_bag_cart_groups(DEFAULT_HOSPITAL_BAG_CART_GROUPS)


def _clone_hospital_bag_cart_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": _text(group.get("title")),
            "tone": _text(group.get("tone")) or "rose",
            "items": [_clone_hospital_bag_cart_item(item) for item in group.get("items", []) if isinstance(item, dict)],
        }
        for group in groups
        if isinstance(group, dict)
    ]


def _clone_hospital_bag_cart_item(item: dict[str, Any]) -> dict[str, Any]:
    cloned = _cart_item(item)
    _apply_default_cart_item_image(cloned)
    return cloned


def _apply_default_cart_item_image(item: dict[str, Any]) -> None:
    if item.get("image_url"):
        return
    item_id = _text(item.get("id"))
    image_url = MOMCOZY_PUMP_IMAGE_URLS.get(item_id) or HOSPITAL_BAG_CART_PRODUCT_IMAGE_URLS.get(item_id)
    if not image_url:
        return
    item["image_url"] = image_url
    if not item.get("image_alt"):
        item["image_alt"] = _text(item.get("name")) or "商品图"


def _cart_item_line_total_cny(item: dict[str, Any], qty: int) -> float:
    price = _number(item.get("price"), default=0)
    if _cart_item_currency(item) == "USD":
        price = _usd_to_cny(price)
    return round(price * qty, 2)


def _cart_item_currency(item: dict[str, Any]) -> str:
    currency = _text(item.get("currency")).upper() or "CNY"
    return currency if currency in {"USD", "CNY"} else "CNY"


def _hospital_bag_cart_update_envelope(
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
    return {"tool_name": "hospital_bag_cart_update", "status": "cart_updated", "summary": message, "cart_update": cart_update}


def _cart_needs_clarification(message: str) -> dict[str, Any]:
    return {
        "tool_name": "hospital_bag_cart_update",
        "status": "needs_clarification",
        "summary": message,
        "cart_update": {"action": "clarify", "message": message},
    }


def _cart_unchanged(message: str) -> dict[str, Any]:
    return {
        "tool_name": "hospital_bag_cart_update",
        "status": "cart_unchanged",
        "summary": message,
        "cart_update": {"action": "clarify", "message": message},
    }


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
        price = float(product["price_usd"])
        if use_case in set(product.get("use_cases") or []):
            score += 5
        if preference in set(product.get("preferences") or []):
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
            score += 5 if price <= target_budget_usd else -min(8, (price - target_budget_usd) / 25)
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
    reason_parts = [_text(product.get("best_for"))]
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
    if any(_text(item.get("sku_id")) == "pump-air-1" for item in products):
        air = _momcozy_pump_product("pump-air-1") or product
        return (
            f"Air 1 是高价轻薄款，官方价折合约 {_pump_price_cny_label(air)}；"
            "不能把 Air 1 描述为降低预算或省钱选择。"
            "若用户要省预算，应优先说明 S9 Pro、S12 Pro Quick 等更低价型号。"
        )
    return "价格比较必须按 official_price_usd / sale_price_usd 和 price_label / sale_price_label 数值说明；不要把更高价型号描述为省预算。"


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
        aliases = [product["sku_id"], product["model"], product["name"], str(product["model"]).replace(" ", "")]
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


def _upsert_hospital_bag_pump_model(groups: list[dict[str, Any]], product: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
            item_id = _text(item.get("id"))
            if item_id == next_item["id"]:
                return next_groups, {"mode": "unchanged", "item_id": next_item["id"], "name": next_item["name"]}
            if item_id in pump_ids or "吸奶器" in _text(item.get("name")):
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


def _pump_price_position(product: dict[str, Any]) -> str:
    sku_id = _text(product.get("sku_id"))
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
    sku_id = _text(product.get("sku_id"))
    if sku_id == "pump-air-1":
        return "Air 1 是高价轻薄/隐蔽升级选择，不适合描述为降低预算；预算优先时应看 S9 Pro 或 S12 Pro Quick。"
    if sku_id in {"pump-s9-pro", "pump-s12-pro-quick"}:
        return "预算优先时更适合优先考虑。"
    return "按功能、舒适度和预算综合比较。"


def _pump_price_cny_label(product: dict[str, Any]) -> str:
    return _money_label(_usd_to_cny(product["price_usd"]), "CNY")


def _pump_sale_price_cny_label(product: dict[str, Any]) -> str:
    sale_price = product.get("sale_price_usd")
    if not isinstance(sale_price, (int, float)):
        return ""
    prefix = "约 " if product.get("supports_single_unit") else ""
    return f"{prefix}{_money_label(_usd_to_cny(sale_price), 'CNY')}"


def _usd_to_cny(value: Any) -> float:
    return round(_number(value, default=0) * HOSPITAL_BAG_CART_USD_TO_CNY_RATE, 2)


def _cart_totals_label(totals: dict[str, Any]) -> str:
    return _money_label(totals.get("total"), "CNY")


def _money_label(value: Any, currency: str) -> str:
    amount = _number(value, default=0)
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
    before_totals = _cart_totals(groups)
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
            totals = _cart_totals(next_groups)
            if totals["total"] <= target_budget:
                break
            next_groups_candidate, names = _remove_hospital_bag_cart_items(next_groups, [item_id])
            if not names:
                continue
            next_groups = next_groups_candidate
            removed_ids.append(item_id)
            removed_names.extend(names)

    totals = _cart_totals(next_groups)
    return {
        "groups": next_groups,
        "before_totals": before_totals,
        "totals": totals,
        "target_budget": target_budget,
        "budget_met": target_budget is None or totals["total"] <= target_budget,
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
        return f"{prefix}{'，'.join(changed_parts)}；吸奶器我先保留。现在预计合计约 {_cart_totals_label(totals)}，共 {totals['itemCount']} 件。"
    return f"{prefix}当前购物车已经比较接近这个要求，吸奶器我先保留。现在预计合计约 {_cart_totals_label(totals)}，共 {totals['itemCount']} 件。"


def _target_budget(value: Any) -> float | None:
    budget = _number(value, default=0)
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


def _replace_hospital_bag_cart_items(groups: list[dict[str, Any]], item_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requested = set(item_ids) if item_ids else set(HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS)
    replaced_items: list[dict[str, Any]] = []
    next_groups: list[dict[str, Any]] = []
    for group in groups:
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = _text(item.get("id"))
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
            if _text(item.get("id")) in requested:
                name = _first_text(item.get("name"))
                if name:
                    removed_names.append(name)
                continue
            items.append(dict(item))
        next_groups.append({**group, "items": items})
    return next_groups, removed_names


def _restore_hospital_bag_cart_items(groups: list[dict[str, Any]], item_ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    next_groups = _clone_hospital_bag_cart_groups(groups)
    existing_ids = {_text(item.get("id")) for group in next_groups for item in group.get("items", []) if isinstance(item, dict)}
    restored_names: list[str] = []
    for item_id in item_ids:
        if item_id in existing_ids:
            continue
        original_id = HOSPITAL_BAG_CART_REPLACEMENT_ORIGINAL_BY_ID.get(item_id, item_id)
        default = _default_hospital_bag_cart_item(original_id)
        if default is None:
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
        if item_id:
            update_by_id[item_id] = max(0, int(_number(update.get("qty"), default=1)))

    updated_names: list[str] = []
    removed_names: list[str] = []
    next_groups: list[dict[str, Any]] = []
    for group in groups:
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = _text(item.get("id"))
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
            if isinstance(item, dict) and _text(item.get("id")) == item_id:
                return _text(group.get("title")), _text(group.get("tone")) or "rose", _clone_hospital_bag_cart_item(item)
    return None


def _find_or_create_hospital_bag_cart_group(groups: list[dict[str, Any]], title: str, tone: str) -> dict[str, Any]:
    for group in groups:
        if _text(group.get("title")) == title:
            return group
    group = {"title": title, "tone": tone, "items": []}
    groups.append(group)
    return group


def _ids_for_names(groups: list[dict[str, Any]], names: list[str]) -> list[str]:
    wanted = set(names)
    ids: list[str] = []
    for group in groups:
        for item in group.get("items", []):
            if isinstance(item, dict) and _first_text(item.get("name")) in wanted:
                ids.append(_text(item.get("id")))
    return [item_id for item_id in ids if item_id]


def _item_match_key(value: Any) -> str:
    return "".join(ch.lower() for ch in _text(value) if ch.isalnum())


def _number(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _text(value)
    return [text] if text else []


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            nested = _first_text(*value)
            if nested:
                return nested
        elif isinstance(value, dict):
            nested = _first_text(*value.values())
            if nested:
                return nested
        else:
            text = _text(value)
            if text:
                return text
    return ""


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _has_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    return bool(_text(value))
