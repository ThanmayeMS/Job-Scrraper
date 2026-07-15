from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str  # plain str on output; input is validated as EmailStr in UserCreate
    full_name: str | None = None
    is_active: bool
    is_superuser: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
