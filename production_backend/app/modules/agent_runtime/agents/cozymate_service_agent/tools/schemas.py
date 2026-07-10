from __future__ import annotations

from copy import deepcopy
from typing import Any

from production_backend.app.core.errors import ApiError


JsonSchema = dict[str, Any]


_TOOL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "ProfileContextQuery": {
        "title": "ProfileContextQuery",
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "ProfileUpdate": {
        "title": "ProfileUpdate",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "display_name": {"type": "string", "minLength": 1, "maxLength": 120},
            "age": {"type": "integer", "minimum": 12, "maximum": 70},
            "onboarding_skipped": {
                "type": "boolean",
                "description": "用户明确说先跳过名字或年龄时传 true。",
            },
        },
    },
    "BusinessContextQuery": {
        "title": "BusinessContextQuery",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "每个业务上下文分区最多返回的条目数。",
            }
        },
    },
    "MilkSummaryQuery": {
        "title": "MilkSummaryQuery",
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
    "MilkStatusQuery": {
        "title": "MilkStatusQuery",
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
    "MilkAnalysisReadQuery": {
        "title": "MilkAnalysisReadQuery",
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
    "GrowthRecordsQuery": {
        "title": "GrowthRecordsQuery",
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
    "PlansCurrentQuery": {
        "title": "PlansCurrentQuery",
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
    "PlansCalendarQuery": {
        "title": "PlansCalendarQuery",
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
    "DiaryRecentQuery": {
        "title": "DiaryRecentQuery",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "最多纳入的近期日记条目数。",
            }
        },
    },
    "PregnancyPlanContextQuery": {
        "title": "PregnancyPlanContextQuery",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "最多纳入的当前计划、任务和日记条目数。",
            }
        },
    },
    "DiaryEntryUpsertProposalCreate": {
        "title": "DiaryEntryUpsertProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["entry_date"],
        "properties": {
            "entry_date": {"type": "string", "minLength": 1, "maxLength": 20},
            "gestational_week": {"type": "string", "maxLength": 32},
            "mood": {"type": "string", "maxLength": 64},
            "energy_level": {"type": "string", "maxLength": 64},
            "sleep_summary": {"type": "string", "maxLength": 2000},
            "fetal_movement": {"type": "string", "maxLength": 2000},
            "symptom_tags": {"type": "array", "items": {}},
            "appointment_note": {"type": "string", "maxLength": 2000},
            "nutrition_note": {"type": "string", "maxLength": 2000},
            "content": {"type": "string", "maxLength": 5000},
            "attachments": {"type": "array", "items": {}},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "MemoryCreateProposalCreate": {
        "title": "MemoryCreateProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["memory_type", "content"],
        "properties": {
            "memory_type": {
                "type": "string",
                "enum": [
                    "user_preference",
                    "stable_care_preference",
                    "communication_preference",
                    "recurring_constraint",
                ],
            },
            "content": {
                "type": "object",
                "additionalProperties": True,
                "required": ["summary"],
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
            "confidence_score": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
            "sensitivity": {
                "type": "string",
                "enum": ["normal", "personal", "health", "child", "crisis", "regulated", "financial", "legal"],
                "default": "normal",
                "description": "拟写入记忆的敏感度分类；高敏感类型会被策略门控。",
            },
            "expires_in_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "description": "短期记忆记录的可选有效天数。",
            },
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "DevicesPumpStatusQuery": {
        "title": "DevicesPumpStatusQuery",
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
    "DeviceGuidanceAssetsQuery": {
        "title": "DeviceGuidanceAssetsQuery",
        "type": "object",
        "additionalProperties": False,
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
                "maxLength": 120,
                "description": "可选设备型号过滤条件，例如 Air1 或 BP334。",
            },
            "topic": {
                "type": "string",
                "maxLength": 80,
                "description": "可选指导主题，例如 setup、cleaning、flange、suction 或 bluetooth。",
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
    "FileVisionSummaryQuery": {
        "title": "FileVisionSummaryQuery",
        "type": "object",
        "additionalProperties": False,
        "required": ["file_id"],
        "properties": {
            "file_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": "需要摘要的当前用户范围内上传图片 file_id。",
            }
        },
    },
    "MilkPlanProposalCreate": {
        "title": "MilkPlanProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 255},
            "summary": {"type": "string", "maxLength": 2000},
            "payload": {
                "type": "object",
                "additionalProperties": True,
            },
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "MilkPlanPreviewCreate": {
        "title": "MilkPlanPreviewCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 255},
            "summary": {"type": "string", "maxLength": 2000},
            "direction": {"type": "string", "enum": ["increase", "maintain", "decrease", "observe", "unknown"]},
            "start_date": {"type": "string", "maxLength": 20},
            "days": {"type": "integer", "minimum": 1, "maximum": 30},
            "tasks": {"type": "array", "maxItems": 40, "items": {"type": "object", "additionalProperties": True}},
            "reminders": {"type": "array", "maxItems": 40, "items": {"type": "object", "additionalProperties": True}},
            "payload": {"type": "object", "additionalProperties": True},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
        },
    },
    "PregnancyPlanProposalCreate": {
        "title": "PregnancyPlanProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 255},
            "summary": {"type": "string", "maxLength": 2000},
            "payload": {
                "type": "object",
                "additionalProperties": True,
            },
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "PlanTaskCreateProposalCreate": {
        "title": "PlanTaskCreateProposalCreate",
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
    "PlanTaskCompleteProposalCreate": {
        "title": "PlanTaskCompleteProposalCreate",
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
    "PlanTaskUpdateProposalCreate": {
        "title": "PlanTaskUpdateProposalCreate",
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
    "PlanTaskDeleteProposalCreate": {
        "title": "PlanTaskDeleteProposalCreate",
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
    "PlanDeleteProposalCreate": {
        "title": "PlanDeleteProposalCreate",
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
    "MilkReminderProposalCreate": {
        "title": "MilkReminderProposalCreate",
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
    "FeedingRecordProposalCreate": {
        "title": "FeedingRecordProposalCreate",
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
    "PumpingRecordProposalCreate": {
        "title": "PumpingRecordProposalCreate",
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
    "RecordDeleteProposalCreate": {
        "title": "RecordDeleteProposalCreate",
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
    "GrowthRecordProposalCreate": {
        "title": "GrowthRecordProposalCreate",
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
    "GrowthRecordUpdateProposalCreate": {
        "title": "GrowthRecordUpdateProposalCreate",
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
    "HospitalBagCartUpdateProposalCreate": {
        "title": "HospitalBagCartUpdateProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["cart_update"],
        "properties": {
            "cart_update": {
                "type": "object",
                "description": "用于预览并在用户确认后应用的最小购物车变更。",
                "additionalProperties": True,
            },
            "summary": {
                "type": "string",
                "description": "面向用户展示的简短购物车变更说明。",
                "maxLength": 500,
            },
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "LegacyArtifactToolInput": {
        "title": "LegacyArtifactToolInput",
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "default_values": {
                "type": "object",
                "additionalProperties": True,
                "description": "旧表单工具用于预填字段的默认值。",
            },
            "confirmed_form_data": {
                "type": "object",
                "additionalProperties": True,
                "description": "用户已经确认并提交的旧表单数据。",
            },
            "form_data": {
                "type": "object",
                "additionalProperties": True,
                "description": "confirmed_form_data 的兼容别名。",
            },
            "plan_context": {
                "type": "object",
                "additionalProperties": True,
                "description": "生成孕期计划卡时使用的结构化上下文。",
            },
            "payload": {
                "type": "object",
                "additionalProperties": True,
                "description": "旧工具的补充结构化载荷。",
            },
            "groups": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
                "description": "旧待产包购物车分组。",
            },
            "action": {
                "type": "string",
                "enum": [
                    "replace_pump_model",
                    "add_pump_model",
                    "optimize_budget",
                    "apply_budget_plan",
                    "remove_items",
                    "restore_items",
                    "replace_items",
                    "mark_provided",
                    "mark_owned",
                    "update_quantity",
                    "reset_cart",
                    "clarify",
                ],
                "description": "旧待产包购物车动作；表单/卡片工具可忽略。",
            },
            "item_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要操作的当前购物车 item_id；预算优化或不需要指定物品时传空数组。",
            },
            "product_sku_id": {
                "type": ["string", "null"],
                "description": "action=replace_pump_model 或 add_pump_model 时使用；可取 pump-s9-pro、pump-s12-pro-quick、pump-m5-smart、pump-m6、pump-v1-pro、pump-v2-pro、pump-m9、pump-w1、pump-air-1。",
            },
            "quantity_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "item_id": {"type": "string"},
                        "qty": {"type": "integer", "minimum": 0},
                    },
                },
                "description": "action=update_quantity 时使用；qty=0 表示移除。",
            },
            "target_budget": {
                "type": ["number", "null"],
                "description": "用户明确预算上限，例如 1000；没有明确预算时传 null。",
            },
            "budget_mode": {
                "type": "string",
                "enum": ["under", "around", "cheaper", "minimal", "none"],
                "description": "预算意图；没有预算相关意图时传 none。",
            },
            "preference": {
                "type": "string",
                "enum": ["balanced", "cheapest", "comfort", "breastfeeding", "minimal", "budget", "portable", "performance", "app", "simple", "premium"],
                "description": "用户偏好；不确定时传 balanced。",
            },
            "preserve_item_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "用户明确想保留的 item_id；不确定传空数组。",
            },
            "allow_remove_pump": {
                "type": "boolean",
                "description": "只有用户明确同意吸奶器后买/删除吸奶器，或预算低到必须移除且用户确认时才传 true。",
            },
            "message": {"type": "string", "maxLength": 1000},
            "summary": {"type": "string", "maxLength": 1000},
            "generation_mode": {"type": "string", "maxLength": 80},
            "requested_model": {
                "type": ["string", "null"],
                "maxLength": 120,
                "description": "用户点名询问或追问的型号，例如 Air 1、Air1、M9、S12 Pro Quick；没有点名型号传 null。",
            },
            "use_case": {
                "type": "string",
                "enum": ["unknown", "hospital_backup", "daily_home", "work_pumping", "portable", "comfort", "performance", "high_output", "budget"],
            },
            "feeding_intention": {
                "type": "string",
                "enum": ["unknown", "breastfeeding", "mixed", "formula"],
            },
            "target_budget_usd": {"type": ["number", "null"]},
            "must_have_app": {"type": ["boolean", "null"]},
            "need_single_unit": {"type": ["boolean", "null"]},
            "scope": {"type": "string", "enum": ["full", "prenatal_only", "short_range"]},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
        },
    },
    "AgentArtifactCreate": {
        "title": "AgentArtifactCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 255},
            "summary": {"type": "string", "maxLength": 2000},
            "sections": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                },
            },
            "payload": {
                "type": "object",
                "additionalProperties": True,
                "description": "可选结构化载荷，用于承载不适合放入 title、summary 或 sections 的字段。",
            },
            "source_context": {
                "type": "object",
                "additionalProperties": True,
                "description": "用于生成产物的简短、非敏感事实。",
            },
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
        },
    },
    "SupportTicketProposalCreate": {
        "title": "SupportTicketProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["issue_summary"],
        "properties": {
            "issue_type": {
                "type": "string",
                "description": "问题的简短分类，例如 pump、order、app 或 other。",
                "maxLength": 80,
            },
            "issue_summary": {
                "type": "string",
                "description": "确认前展示给用户看的简短问题摘要。",
                "minLength": 1,
                "maxLength": 500,
            },
            "product_model": {"type": "string", "maxLength": 120},
            "order_number": {"type": "string", "maxLength": 120},
            "purchase_channel": {"type": "string", "maxLength": 120},
            "user_contact": {
                "type": "string",
                "description": "可选联系方式；在预览载荷中只展示为布尔状态。",
                "maxLength": 255,
            },
            "urgency": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "payload": {
                "type": "object",
                "additionalProperties": True,
            },
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "IbclcConsultCardCreate": {
        "title": "IbclcConsultCardCreate",
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


def tool_input_schema(schema_ref: str) -> JsonSchema:
    schema = _TOOL_INPUT_SCHEMAS.get(schema_ref)
    if schema is None:
        raise ApiError(code="tool_schema_not_found", message="Tool input schema is not registered.", status=500)
    return deepcopy(schema)


def validate_tool_input(*, schema_ref: str, value: dict[str, Any]) -> None:
    schema = tool_input_schema(schema_ref)
    _validate_value(schema=schema, value=value, path="$")


def _validate_value(*, schema: JsonSchema, value: Any, path: str) -> None:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            _raise_invalid(path=path, reason="must be an object")
        _validate_object(schema=schema, value=value, path=path)
        return
    if schema_type == "string":
        _validate_string(schema=schema, value=value, path=path)
        return
    if schema_type == "integer":
        _validate_integer(schema=schema, value=value, path=path)
        return
    if schema_type == "number":
        _validate_number(schema=schema, value=value, path=path)
        return
    if schema_type == "array":
        _validate_array(schema=schema, value=value, path=path)
        return
    if schema_type == "boolean":
        _validate_boolean(value=value, path=path)


def _validate_object(*, schema: JsonSchema, value: dict[str, Any], path: str) -> None:
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    missing = [key for key in required if key not in value]
    if missing:
        _raise_invalid(path=path, reason=f"missing required field: {missing[0]}")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            _raise_invalid(path=f"{path}.{extra[0]}", reason="field is not allowed")
    for key, item in value.items():
        child_schema = properties.get(key)
        if isinstance(child_schema, dict):
            _validate_value(schema=child_schema, value=item, path=f"{path}.{key}")


def _validate_string(*, schema: JsonSchema, value: Any, path: str) -> None:
    if not isinstance(value, str):
        _raise_invalid(path=path, reason="must be a string")
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        _raise_invalid(path=path, reason=f"must be at least {min_length} characters")
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        _raise_invalid(path=path, reason=f"must be at most {max_length} characters")
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        _raise_invalid(path=path, reason="has an unsupported value")


def _validate_integer(*, schema: JsonSchema, value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _raise_invalid(path=path, reason="must be an integer")
    minimum = schema.get("minimum")
    if isinstance(minimum, int) and value < minimum:
        _raise_invalid(path=path, reason=f"must be greater than or equal to {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, int) and value > maximum:
        _raise_invalid(path=path, reason=f"must be less than or equal to {maximum}")


def _validate_number(*, schema: JsonSchema, value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _raise_invalid(path=path, reason="must be a number")
    minimum = schema.get("minimum")
    if isinstance(minimum, int | float) and value < minimum:
        _raise_invalid(path=path, reason=f"must be greater than or equal to {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, int | float) and value > maximum:
        _raise_invalid(path=path, reason=f"must be less than or equal to {maximum}")


def _validate_array(*, schema: JsonSchema, value: Any, path: str) -> None:
    if not isinstance(value, list):
        _raise_invalid(path=path, reason="must be an array")
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        _raise_invalid(path=path, reason=f"must include at least {min_items} items")
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        _raise_invalid(path=path, reason=f"must include at most {max_items} items")
    item_schema = schema.get("items")
    if isinstance(item_schema, dict) and item_schema:
        for index, item in enumerate(value):
            _validate_value(schema=item_schema, value=item, path=f"{path}[{index}]")


def _validate_boolean(*, value: Any, path: str) -> None:
    if not isinstance(value, bool):
        _raise_invalid(path=path, reason="must be a boolean")


def _raise_invalid(*, path: str, reason: str) -> None:
    raise ApiError(
        code="tool_input_invalid",
        message="Tool input does not match the registered contract.",
        status=422,
        details={"path": path, "reason": reason},
    )
