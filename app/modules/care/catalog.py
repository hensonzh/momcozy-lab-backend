from .schemas import ServicePackageRead

# Versioned editorial catalog from the approved product baseline.
CATALOG = [ServicePackageRead.model_validate(value) for value in [
    {
        "id": "feeding-confidence",
        "name": "Feeding Confidence",
        "subtitle": "Understand your baby's feeding needs",
        "description": "Understand whether your baby is getting enough, identify any feeding changes to consider, and make a feeding plan together.",
        "duration_days": 7,
        "sessions": 2,
        "price_minor": 21900,
        "highlights": [
            "Review whether your baby is getting enough",
            "Create a feeding plan",
            "7 days of follow-up"
        ],
        "expert_services": [
            "One initial video consultation (60 min)",
            "One IBCLC follow-up (20–30 min)"
        ],
        "continuous_services": [
            "Track feeding, pumping, milk supply, and key baby records",
            "Care plan reminders and record keeping",
            "Track how feeding is going",
            "Prepare a progress summary for IBCLC review"
        ]
    },
    {
        "id": "better-breastfeeding",
        "name": "Better Breastfeeding",
        "subtitle": "Build a more comfortable latch",
        "description": "Explore what makes nursing difficult and work on latch, sucking, and feeding comfort.",
        "duration_days": 14,
        "sessions": 2,
        "price_minor": 23900,
        "highlights": [
            "Identify nursing challenges",
            "Work on latch and sucking",
            "14 days of follow-up"
        ],
        "expert_services": [
            "One initial video consultation (60 min)",
            "One IBCLC follow-up (20–30 min)"
        ],
        "continuous_services": [
            "Track nursing frequency and how it feels",
            "Reminders to practice and adjust your care plan",
            "Track changes in your nursing experience",
            "Prepare a progress summary for IBCLC review"
        ]
    },
    {
        "id": "milk-supply-care",
        "name": "Milk Supply Care",
        "subtitle": "Find a milk supply plan that works for you",
        "description": "Explore milk supply concerns and build a personalized plan that can be adjusted over time.",
        "duration_days": 14,
        "sessions": 3,
        "price_minor": 29900,
        "highlights": [
            "Explore milk supply concerns",
            "Build a personalized plan",
            "14 days of adjustments"
        ],
        "expert_services": [
            "One initial video consultation (60 min)",
            "Two IBCLC follow-ups (20–30 min each)"
        ],
        "continuous_services": [
            "Track pumping, milk amounts, and supply-and-demand trends",
            "Care plan reminders and record keeping",
            "Review milk supply and care plan progress",
            "Prepare a progress summary for IBCLC review"
        ]
    },
    {
        "id": "comfortable-feeding",
        "name": "Comfortable Feeding",
        "subtitle": "Ease feeding discomfort with expert support",
        "description": "Identify what may be causing discomfort, work toward more comfortable feeding, and recognize when medical care may be needed.",
        "duration_days": 3,
        "sessions": 2,
        "price_minor": 21900,
        "highlights": [
            "Explore causes of discomfort",
            "Work toward more comfortable feeding",
            "Recognize when a referral may help"
        ],
        "expert_services": [
            "One initial video consultation (60 min)",
            "One IBCLC follow-up (20–30 min)"
        ],
        "continuous_services": [
            "Check in on symptoms regularly",
            "Care plan reminders and observation",
            "Track pain, fullness, and blocked-duct concerns",
            "Prepare a progress summary for IBCLC review"
        ]
    }
]]
PACKAGES = {package.id: package for package in CATALOG}
