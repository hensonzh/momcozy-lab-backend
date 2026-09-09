from .schemas import ServicePackageRead

# Versioned editorial catalog from the approved product baseline.
CATALOG = [ServicePackageRead.model_validate(value) for value in [
    {
        "id": "feeding-confidence",
        "name": "喂养安心",
        "subtitle": "Feeding Confidence",
        "description": "判断宝宝当前是否吃够，明确是否需要调整喂养，并形成 Feeding Plan。",
        "duration_days": 7,
        "sessions": 2,
        "price_minor": 21900,
        "highlights": [
            "判断宝宝是否吃够",
            "形成 Feeding Plan",
            "7 天持续跟进"
        ],
        "expert_services": [
            "1 次首次视频咨询（60 分钟）",
            "1 次 IBCLC 跟进（20–30 分钟）"
        ],
        "continuous_services": [
            "跟踪 feeding、pumping、奶量及宝宝关键记录",
            "按 Care Plan 提醒执行与记录",
            "收集喂养反馈",
            "生成阶段总结供 IBCLC 复核"
        ]
    },
    {
        "id": "better-breastfeeding",
        "name": "亲喂改善",
        "subtitle": "Better Breastfeeding",
        "description": "找到影响亲喂的主要问题，改善含乳、吸吮和喂养体验。",
        "duration_days": 14,
        "sessions": 2,
        "price_minor": 23900,
        "highlights": [
            "定位亲喂问题",
            "改善含乳与吸吮",
            "14 天持续跟进"
        ],
        "expert_services": [
            "1 次首次视频咨询（60 分钟）",
            "1 次 IBCLC 跟进（20–30 分钟）"
        ],
        "continuous_services": [
            "跟踪亲喂频次及主观反馈",
            "按 Care Plan 提醒练习与调整",
            "收集亲喂体验和问题变化",
            "生成阶段总结供 IBCLC 复核"
        ]
    },
    {
        "id": "milk-supply-care",
        "name": "奶量管理",
        "subtitle": "Milk Supply Care",
        "description": "判断奶量问题，建立并持续调整个性化奶量管理方案。",
        "duration_days": 14,
        "sessions": 3,
        "price_minor": 29900,
        "highlights": [
            "判断奶量问题",
            "建立个性化方案",
            "14 天持续调整"
        ],
        "expert_services": [
            "1 次首次视频咨询（60 分钟）",
            "2 次 IBCLC 跟进（20–30 分钟）"
        ],
        "continuous_services": [
            "跟踪 pumping、奶量及供需趋势",
            "按 Care Plan 提醒执行与记录",
            "汇总奶量及计划执行变化",
            "生成阶段总结供 IBCLC 复核"
        ]
    },
    {
        "id": "comfortable-feeding",
        "name": "舒适哺乳支持",
        "subtitle": "Comfortable Feeding",
        "description": "找到影响舒适度的主要因素，改善不适并及时识别医疗转介需求。",
        "duration_days": 3,
        "sessions": 2,
        "price_minor": 21900,
        "highlights": [
            "定位不适因素",
            "改善哺乳舒适度",
            "识别转介需求"
        ],
        "expert_services": [
            "1 次首次视频咨询（60 分钟）",
            "1 次 IBCLC 跟进（20–30 分钟）"
        ],
        "continuous_services": [
            "定期发起症状回访",
            "按 Care Plan 提醒执行与观察",
            "收集疼痛、胀奶、堵奶等反馈",
            "生成阶段总结供 IBCLC 复核"
        ]
    }
]]
PACKAGES = {package.id: package for package in CATALOG}
