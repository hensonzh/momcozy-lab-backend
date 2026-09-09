from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr


class WorkbenchLoginWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=120)


class WorkbenchChallengeRead(BaseModel):
    challenge: str = Field(repr=False)
    expires_at: AwareDatetime


class WorkbenchVerifyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: SecretStr = Field(min_length=40, max_length=128)
    code: SecretStr = Field(min_length=6, max_length=6)
