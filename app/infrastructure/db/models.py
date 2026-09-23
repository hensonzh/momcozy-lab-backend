from __future__ import annotations

from ...modules.audit import models as audit_models
from ...modules.appointments import models as appointment_models
from ...modules.consultations import models as consultation_models
from ...modules.consultations import room_models
from ...modules.documentation import models as documentation_models
from ...modules.auth import models as auth_models
from ...modules.auth import workbench_models as workbench_auth_models
from ...modules.devices import models as devices_models
from ...modules.files import models as files_models
from ...modules.invites import models as invites_models
from ...modules.notifications import models as notifications_models
from ...modules.care import models as care_models
from ...modules.care import event_models as care_event_models
from ...modules.reports import models as report_models
from ...modules.lactation import models as lactation_models
from ...modules.baby import models as baby_models
from ...modules.baby import profile_models as baby_profile_models
from ...modules.plans import models as plans_models
from ...modules.profiles import models as profiles_models
from ...modules.profiles import me_models as me_experience_models
from ...modules.records import models as records_models
from ...modules.support import models as support_models
from ...modules.users import models as users_models

__all__ = [
    "audit_models",
    "appointment_models",
    "consultation_models",
    "room_models",
    "documentation_models",
    "auth_models",
    "workbench_auth_models",
    "devices_models",
    "files_models",
    "invites_models",
    "notifications_models",
    "care_models",
    "care_event_models",
    "report_models",
    "lactation_models",
    "baby_models",
    "baby_profile_models",
    "plans_models",
    "profiles_models",
    "me_experience_models",
    "records_models",
    "support_models",
    "users_models",
]
