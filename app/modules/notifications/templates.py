from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationTemplate:
    category: str
    title: str
    body: str


# Inbox templates are separate from the generic lock-screen envelope. Locale
# selection is centralized; unsupported locales use English for now.
TEMPLATES = {
    "appointment_created": NotificationTemplate("appointments", "Appointment confirmed", "Your appointment is confirmed. Open the details to prepare."),
    "appointment_reminder": NotificationTemplate("appointments", "Your appointment is coming up", "Open your appointment to review the details and get ready."),
    "consultation_started": NotificationTemplate("consultations", "Your consultation has started", "Open your consultation to join."),
    "consultation_ended": NotificationTemplate("consultations", "Consultation update", "Your consultation has ended. Open the summary for next steps."),
    "expert_feedback": NotificationTemplate("expert_feedback", "New expert feedback", "Your expert has shared an update. Open the app to view it."),
    "service_progress_updated": NotificationTemplate("service_updates", "Service update", "There is an update to your service. Open the details to view it."),
}

SERVICE_CATEGORIES = ("appointments", "consultations", "expert_feedback", "service_updates")
