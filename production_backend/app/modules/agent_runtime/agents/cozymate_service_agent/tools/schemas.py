from __future__ import annotations

from copy import deepcopy
from typing import Any

from production_backend.app.core.errors import ApiError


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
    "profile.read": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "profile_update": {
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
    "records.milk_summary.read": {
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
    "records.milk_status.read": {
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
    "records.milk_analysis.read": {
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
    "records.milk_analysis.intake": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "resume", "answer", "reset"],
                "default": "start",
                "description": "开始、恢复、回答当前唯一问题，或明确重置六项奶量分析采集。",
            },
            "days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
        },
    },
    "records.milk_analysis.evaluate": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "records.growth.read": {
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
    "plans.current.read": {
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
    "plans.calendar.read": {
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
    "pregnancy_diary.manage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "list", "write", "update", "delete"],
                "description": "要执行的日记动作。",
            },
            "entry_date": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
                "description": "read/write/update/delete 使用的 YYYY-MM-DD 日期；省略时使用当前用户本地日期。",
            },
            "start_date": {"type": "string", "maxLength": 20, "description": "list 使用的日期范围起点，格式为 YYYY-MM-DD。"},
            "end_date": {"type": "string", "maxLength": 20, "description": "list 使用的日期范围终点，格式为 YYYY-MM-DD。"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 7,
                "description": "list 最多返回的日记条数。",
            },
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 5000,
                "description": "write/update 使用的完整正文；update 必须包含旧正文和本轮新增事实的完整重写结果。",
            },
            "confirmed": {
                "type": "boolean",
                "default": False,
                "description": "只有用户明确确认删除时才为 true；其他动作保持 false 或省略。",
            },
        },
    },
    "devices.pump_status.read": {
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
    "devices.guidance.read": {
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
    "devices.unboxing.advance": {
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
    "images.inspect": {
        "type": "object",
        "additionalProperties": False,
        "required": ["image_url"],
        "properties": {
            "image_url": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "当前可见对话历史中已经展示过的图片 URL。",
            },
            "detail": {
                "type": "string",
                "enum": ["low", "high"],
                "default": "low",
                "description": "普通内容识别使用 low；小字或精细结构识别使用 high。",
            },
        },
    },
    "plans.milk_plan.propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "direction", "tasks"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 255},
            "summary": {"type": "string", "maxLength": 2000},
            "direction": {"type": "string", "enum": ["increase", "maintain", "decrease", "observe", "unknown"]},
            "start_date": {"type": "string", "maxLength": 20},
            "days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7},
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "time", "task_type"],
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 255},
                        "time": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
                        "task_type": {"type": "string", "enum": ["pumping", "feeding", "other"]},
                        "description": {"type": "string", "maxLength": 2000},
                        "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                        "day": {"type": "integer", "minimum": 1, "maximum": 30},
                        "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 240},
                    },
                },
            },
            "reminders": {"type": "array", "maxItems": 40, "items": {"type": "object", "additionalProperties": True}},
        },
    },
    "plans.milk_schedule.propose": {
        "type": "object",
        "additionalProperties": False,
        "required": ["plan_id", "busy_windows"],
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
            "min_gap_minutes": {"type": "integer", "minimum": 0, "maximum": 360, "default": 90},
            "default_duration_minutes": {"type": "integer", "minimum": 1, "maximum": 240, "default": 30},
        },
    },
    "pregnancy.plan.propose": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string", "maxLength": 2000},
            "scope": {"type": "string", "enum": ["full", "prenatal_only", "short_range"]},
            "additional_info": {
                "type": "string",
                "maxLength": 2000,
                "description": "用户在针对性分析后的最后一轮主动补充；没有补充时省略。",
            },
        },
    },
    "pregnancy.plan_intake.start": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "restart": {
                "type": "boolean",
                "description": "仅当用户明确要求放弃当前采集并重新开始时设为 true。",
            },
        },
    },
    "pregnancy.plan_intake.analyze": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "pregnancy.plan_intake.advance": {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "submit_personalized_followup",
                    "finish_personalized_followups",
                    "confirm_checkup_done",
                    "confirm_no_checkup_yet",
                    "confirm_checkup_unknown",
                    "mark_checkup_records_uploaded",
                    "skip_checkup_records",
                    "confirm_ready_to_generate",
                    "submit_final_additional_info",
                    "abandon",
                ],
            },
            "answer": {"type": "string", "maxLength": 2000},
            "summary": {"type": "string", "maxLength": 2000},
            "additional_info": {"type": "string", "maxLength": 2000},
        },
    },
    "plans.task_create.propose": {
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
    "plans.task_complete.propose": {
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
    "pregnancy.plan_todo.propose": {
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
    "plans.task_update.propose": {
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
    "plans.task_delete.propose": {
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
    "plans.plan_delete.propose": {
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
    "notifications.milk_reminder.propose": {
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
    "records.feeding_record.propose": {
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
    "records.pumping_record.propose": {
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
    "records.feeding_record_delete.propose": {
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
    "records.growth_record.propose": {
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
    "records.growth_record_update.propose": {
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
    "support.ticket.propose": {
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


for _tool_name in ("records.pumping_record_delete.propose", "records.growth_record_delete.propose"):
    _TOOL_INPUT_SCHEMAS[_tool_name] = _TOOL_INPUT_SCHEMAS["records.feeding_record_delete.propose"]

_TOOL_INPUT_SCHEMAS["plans.milk_task_update.propose"] = _TOOL_INPUT_SCHEMAS["plans.task_update.propose"]
_TOOL_INPUT_SCHEMAS["plans.milk_task_delete.propose"] = _TOOL_INPUT_SCHEMAS["plans.task_delete.propose"]

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


def validate_tool_input(*, schema: JsonSchema, value: dict[str, Any]) -> None:
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
