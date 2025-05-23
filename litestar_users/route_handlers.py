from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Union, cast

from litestar import Request, Response, Router, delete, get, patch, post, put, status_codes
from litestar.di import Provide
from litestar.enums import MediaType
from litestar.exceptions import (
    HTTPException,
    ImproperlyConfiguredException,
    NotAuthorizedException,
    PermissionDeniedException,
)
from litestar.params import Parameter
from litestar.security.jwt import JWTAuth, JWTCookieAuth
from litestar.security.session_auth.auth import SessionAuth

from litestar_users.adapter.sqlalchemy.protocols import SQLARoleT, SQLAUserT
from litestar_users.dependencies import provide_user_service
from litestar_users.dtos import OAuthAuthorizeDTO
from litestar_users.schema import OAuth2AuthorizeSchema

__all__ = [
    "get_auth_handler",
    "get_current_user_handler",
    "get_password_reset_handler",
    "get_registration_handler",
    "get_role_management_handler",
    "get_user_management_handler",
    "get_verification_handler",
]


if TYPE_CHECKING:
    from uuid import UUID

    from advanced_alchemy.extensions.litestar.dto import SQLAlchemyDTO
    from httpx_oauth.oauth2 import BaseOAuth2
    from litestar.contrib.pydantic import PydanticDTO
    from litestar.dto import DataclassDTO, DTOData, MsgspecDTO
    from litestar.handlers import HTTPRouteHandler
    from litestar.types import Guard

    from litestar_users.protocols import UserRegisterT
    from litestar_users.schema import (
        ForgotPasswordSchema,
        ResetPasswordSchema,
        UserRoleSchema,
    )
    from litestar_users.service import UserServiceType


def get_registration_handler(
    path: str,
    user_registration_dto: type[DataclassDTO | MsgspecDTO | PydanticDTO],
    user_read_dto: type[SQLAlchemyDTO],
    tags: list[str] | None = None,
) -> HTTPRouteHandler:
    """Get registration route handlers.

    Args:
        path: The path for the router.
        user_registration_dto: A subclass of [UserCreateDTO][litestar_users.schema.UserCreateDTO]
        user_read_dto: A subclass of [UserReadDTO][litestar_users.schema.UserReadDTO]
        tags: A list of string tags to append to the schema of the route handler.
    """

    @post(
        path,
        dto=user_registration_dto,
        return_dto=user_read_dto,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        exclude_from_auth=True,
        tags=tags,
    )
    async def register(data: DTOData[UserRegisterT], service: UserServiceType, request: Request) -> SQLAUserT:
        """Register a new user."""
        return cast(SQLAUserT, await service.register(data.as_builtins(), request))

    return register


def get_oauth2_handler(
    path: str,
    oauth_client: BaseOAuth2,
    user_read_dto: type[SQLAlchemyDTO],  # pyright: ignore
    auth_backend: JWTAuth | JWTCookieAuth | SessionAuth,
    state_secret: str,
    guards: list["Guard"],
    opt: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    redirect_url: str | None = None,
    associate_by_email: bool = False,
    is_verified_by_default: bool = False,
) -> Router:
    """Get OAuth2 route handlers.

    Args:
        path: The path for the router.
        oauth_client: The OAuth2 client to use.
        user_read_dto: A subclass of [UserReadDTO][litestar_users.schema.UserReadDTO]
        auth_backend: A Litestar authentication backend.
        state_secret: The secret to use for the state.
        tags: A list of string tags to append to the schema of the route handler.
        guards: A list of [Guards][litestar.types.Guard] that determines who is authorized to manage roles.
        opt: Optional route handler 'opts' to provide additional context to Guards.
        redirect_url: The redirect URL to use.
        associate_by_email: Whether to associate the user by email, default is False.
        is_verified_by_default: Whether to set the user as verified by default, default is False.
    """
    callback_route_name = f"oauth2:{oauth_client.name}.callback"

    @get(
        f"{path}/{oauth_client.name}/authorize",
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        exclude_from_auth=True,
        return_dto=OAuthAuthorizeDTO,
        guards=guards,
        tags=tags,
        opt=opt,
    )
    async def authorize(
        service: UserServiceType,
        request: Request,
        scopes: Union[list[str], None] = None,
    ) -> OAuth2AuthorizeSchema:
        """OAuth2 route."""
        return await service.oauth2_authorize(
            scopes=scopes,
            request=request,
            oauth_client=oauth_client,
            state_secret=state_secret,
            redirect_url=redirect_url,
            callback_route_name=callback_route_name,
        )

    @get(
        f"{path}/{oauth_client.name}/callback",
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        exclude_from_auth=True,
        name=callback_route_name,
        return_dto=user_read_dto,
        guards=guards,
        tags=tags,
        opt=opt,
    )
    async def callback(
        service: UserServiceType,
        request: Request,
        code_param: Annotated[Union[str, None], Parameter(query="code")] = None,
        code_verifier_param: Annotated[Union[str, None], Parameter(query="code_verifier")] = None,
        state_param: Annotated[Union[str, None], Parameter(query="state")] = None,
        error_param: Annotated[Union[str, None], Parameter(query="error")] = None,
    ) -> Response[SQLAUserT]:
        """OAuth2 callback route."""
        user = await service.oauth2_callback(
            data={
                "code": code_param,
                "code_verifier": code_verifier_param,
                "state": state_param,
                "error": error_param,
            },
            request=request,
            redirect_url=redirect_url,
            callback_route_name=callback_route_name,
            oauth_client=oauth_client,
            state_secret=state_secret,
            associate_by_email=associate_by_email,
            is_verified_by_default=is_verified_by_default,
        )
        if user.is_active is False:
            raise HTTPException(status_code=status_codes.HTTP_400_BAD_REQUEST, detail="User is not active.") from None
        if isinstance(auth_backend, SessionAuth):
            request.set_session({**request.session, "user_id": user.id})
            return Response(
                content=cast(SQLAUserT, user),
                status_code=status_codes.HTTP_201_CREATED,
                media_type=MediaType.JSON,
            )
        return auth_backend.login(identifier=str(user.id), response_body=cast(SQLAUserT, user))

    return Router(path="/", route_handlers=[authorize, callback])


def get_oauth2_associate_handler(
    path: str,
    oauth_client: BaseOAuth2,
    user_read_dto: type[SQLAlchemyDTO],  # pyright: ignore
    auth_backend: JWTAuth | JWTCookieAuth | SessionAuth,
    state_secret: str,
    guards: list["Guard"],
    opt: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    redirect_url: str | None = None,
    associate_by_email: bool = False,
    is_verified_by_default: bool = False,
) -> Router:
    """Get OAuth2 route handlers.

    Args:
        path: The path for the router.
        oauth_client: The OAuth2 client to use.
        user_read_dto: A subclass of [UserReadDTO][litestar_users.schema.UserReadDTO]
        auth_backend: A Litestar authentication backend.
        state_secret: The secret to use for the state.
        guards: A list of [Guards][litestar.types.Guard] that determines who is authorized to manage roles.
        tags: A list of string tags to append to the schema of the route handler.
        opt: Optional route handler 'opts' to provide additional context to Guards.
        redirect_url: The redirect URL to use.
        associate_by_email: Whether to associate the user by email, default is False.
        is_verified_by_default: Whether to set the user as verified by default, default is False.
    """
    callback_route_name = f"oauth2-associate:{oauth_client.name}.callback"

    @get(
        f"{path}-associate/{oauth_client.name}/authorize",
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
        guards=guards,
        return_dto=OAuthAuthorizeDTO,
        opt=opt,
    )
    async def authorize(
        service: UserServiceType,
        request: Request,
        scopes: Union[list[str], None] = None,
    ) -> OAuth2AuthorizeSchema:
        """OAuth2 route."""
        user_id = request.user.id
        return await service.oauth2_authorize(
            scopes=scopes,
            request=request,
            oauth_client=oauth_client,
            state_secret=state_secret,
            redirect_url=redirect_url,
            callback_route_name=callback_route_name,
            state_data={"sub": str(user_id)},
        )

    @get(
        f"{path}-associate/{oauth_client.name}/callback",
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        name=callback_route_name,
        return_dto=user_read_dto,
        guards=guards,
        tags=tags,
        opt=opt,
    )
    async def callback(
        service: UserServiceType,
        request: Request,
        code_param: Annotated[Union[str, None], Parameter(query="code")] = None,
        code_verifier_param: Annotated[Union[str, None], Parameter(query="code_verifier")] = None,
        state_param: Annotated[Union[str, None], Parameter(query="state")] = None,
        error_param: Annotated[Union[str, None], Parameter(query="error")] = None,
    ) -> Response[SQLAUserT]:
        """OAuth2 callback route."""
        user_id = request.user.id
        user = await service.get_user(user_id)
        if user.is_active is False:
            raise HTTPException(status_code=status_codes.HTTP_401_UNAUTHORIZED, detail="User is not active.") from None
        user = await service.oauth2_callback(
            data={
                "code": code_param,
                "code_verifier": code_verifier_param,
                "state": state_param,
                "error": error_param,
            },
            redirect_url=redirect_url,
            callback_route_name=callback_route_name,
            oauth_client=oauth_client,
            state_secret=state_secret,
            request=request,
            associate_by_email=associate_by_email,
            is_verified_by_default=is_verified_by_default,
            is_associate_callback=True,
            associate_user=user,
        )
        if isinstance(auth_backend, SessionAuth):
            request.set_session({**request.session, "user_id": user.id})
            return Response(
                content=cast(SQLAUserT, user),
                status_code=status_codes.HTTP_201_CREATED,
                media_type=MediaType.JSON,
            )
        return auth_backend.login(identifier=str(user.id), response_body=cast(SQLAUserT, user))

    return Router(path="/", route_handlers=[authorize, callback])


def get_verification_handler(
    path: str,
    user_read_dto: type[SQLAlchemyDTO],
    tags: list[str] | None = None,
) -> HTTPRouteHandler:
    """Get verification route handlers.

    Args:
        path: The path for the router.
        user_read_dto: A subclass of [UserReadDTO][litestar_users.schema.UserReadDTO]
        tags: A list of string tags to append to the schema of the route handler.
    """

    @post(
        path,
        return_dto=user_read_dto,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        exclude_from_auth=True,
        tags=tags,
    )
    async def verify(token: str, service: UserServiceType, request: Request) -> SQLAUserT:
        """Verify a user with a given JWT."""

        return cast(SQLAUserT, await service.verify(token, request))

    return verify


def get_auth_handler(
    login_path: str,
    logout_path: str,
    user_read_dto: type[SQLAlchemyDTO],  # pyright: ignore
    auth_backend: JWTAuth | JWTCookieAuth | SessionAuth,
    authentication_schema: Any,
    opt: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Router:
    """Get authentication/login route handlers.

    Args:
        login_path: The path for the login router.
        logout_path: The path for the logout router.
        user_read_dto: A subclass of [UserReadDTO][litestar_users.schema.UserReadDTO]
        auth_backend: A Litestar authentication backend.
        authentication_schema: The object that defines the request body schema.
        opt: Optional route handler 'opts' to provide additional context to Guards.
        tags: A list of string tags to append to the schema of the route handlers.
    """

    @post(
        login_path,
        return_dto=user_read_dto,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        exclude_from_auth=True,
        tags=tags,
        opt=opt,
    )
    async def login_session(
        data: authentication_schema,  # pyright: ignore
        service: UserServiceType,
        request: Request,
    ) -> SQLAUserT:
        """Authenticate a user."""
        if not isinstance(auth_backend, SessionAuth):
            raise ImproperlyConfiguredException("session login can only be used with SesssionAuth")

        user = await service.authenticate(data, request)
        if user is None:
            request.clear_session()
            raise NotAuthorizedException(detail="login failed, invalid input")

        request.set_session({**request.session, "user_id": user.id})
        return cast(SQLAUserT, user)

    @post(
        login_path,
        return_dto=user_read_dto,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        exclude_from_auth=True,
        tags=tags,
        opt=opt,
    )
    async def login_jwt(
        data: authentication_schema,  # pyright: ignore
        service: UserServiceType,
        request: Request,
    ) -> Response[SQLAUserT]:
        """Authenticate a user."""

        if not isinstance(auth_backend, (JWTAuth, JWTCookieAuth)):
            raise ImproperlyConfiguredException("jwt login can only be used with JWTAuth")

        user = await service.authenticate(data, request)
        if user is None:
            raise NotAuthorizedException(detail="login failed, invalid input")

        if user.is_verified is False:
            raise PermissionDeniedException(detail="not verified")

        return auth_backend.login(identifier=str(user.id), response_body=cast(SQLAUserT, user))

    @post(logout_path, tags=tags)
    async def logout(request: Request) -> None:
        """Log an authenticated user out."""
        request.clear_session()

    route_handlers = []
    if isinstance(auth_backend, SessionAuth):
        route_handlers.extend([login_session, logout])
    else:
        route_handlers.append(login_jwt)

    return Router(path="/", route_handlers=route_handlers)


def get_current_user_handler(
    path: str,
    user_read_dto: type[SQLAlchemyDTO],  # pyright: ignore
    user_update_dto: type[SQLAlchemyDTO],  # pyright: ignore
    opt: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Router:
    """Get current-user route handlers.

    Args:
        path: The path for the router.
        user_read_dto: A subclass of [UserReadDTO][litestar_users.schema.UserReadDTO]
        user_update_dto: A subclass of [UserUpdateDTO][litestar_users.schema.UserUpdateDTO]
        opt: Optional route handler 'opts' to provide additional context to Guards.
        tags: A list of string tags to append to the schema of the route handlers.
    """

    @get(path, return_dto=user_read_dto, tags=tags, opt=opt)
    async def get_current_user(request: Request[SQLAUserT, Any, Any]) -> SQLAUserT:
        """Get current user info."""

        return request.user

    @patch(
        path,
        dto=user_update_dto,
        return_dto=user_read_dto,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
        opt=opt,
    )
    async def update_current_user(
        data: SQLAUserT,
        request: Request[SQLAUserT, Any, Any],
        service: UserServiceType,
    ) -> SQLAUserT:
        """Update the current user."""
        data.id = request.user.id  # type: ignore[assignment]
        return cast(SQLAUserT, await service.update_user(data=data))

    return Router(path="/", route_handlers=[get_current_user, update_current_user])


def get_password_reset_handler(forgot_path: str, reset_path: str, tags: list[str] | None = None) -> Router:
    """Get forgot-password and reset-password route handlers.

    Args:
        forgot_path: The path for the forgot-password router.
        reset_path: The path for the reset-password router.
        tags: A list of string tags to append to the schema of the route handlers.
    """

    @post(
        forgot_path,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        exclude_from_auth=True,
        tags=tags,
    )
    async def forgot_password(data: ForgotPasswordSchema, service: UserServiceType) -> None:
        await service.initiate_password_reset(data.email)
        return

    @post(
        reset_path,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        exclude_from_auth=True,
        tags=tags,
    )
    async def reset_password(data: ResetPasswordSchema, service: UserServiceType) -> None:
        await service.reset_password(data.token, data.password)
        return

    return Router(path="/", route_handlers=[forgot_password, reset_password])


def get_user_management_handler(
    path_prefix: str,
    guards: list["Guard"],
    identifier_uri: str,
    user_read_dto: type[SQLAlchemyDTO],  # pyright: ignore
    user_update_dto: type[SQLAlchemyDTO],  # pyright: ignore
    opt: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Router:
    """Get user management route handlers.

    Note:
        Routes are guarded by role authorization.

    Args:
        path_prefix: The path prefix for the routers.
        guards: List of Guard callables to determine who is authorized to manage users.
        identifier_uri: The path specifying the user ID and its type.
        opt: Optional route handler 'opts' to provide additional context to Guards.
        user_read_dto: A subclass of [UserReadDTO][litestar_users.schema.UserReadDTO]
        user_update_dto: A subclass of [UserUpdateDTO][litestar_users.schema.UserUpdateDTO]
        tags: A list of string tags to append to the schema of the route handlers.
    """

    @get(
        path=identifier_uri,
        dto=user_read_dto,
        return_dto=user_read_dto,
        guards=guards,
        opt=opt,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
    )
    async def get_user(user_id: Union[UUID, int], service: UserServiceType) -> SQLAUserT:
        """Get a user by id."""

        return cast(SQLAUserT, await service.get_user(user_id))

    @patch(
        path=identifier_uri,
        dto=user_update_dto,
        return_dto=user_read_dto,
        guards=guards,
        opt=opt,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
    )
    async def update_user(user_id: Union[UUID, int], data: SQLAUserT, service: UserServiceType) -> SQLAUserT:
        """Update a user's attributes."""
        data.id = user_id  # type: ignore[assignment]
        return cast(SQLAUserT, await service.update_user(data))

    @delete(
        path=identifier_uri,
        return_dto=user_read_dto,
        status_code=200,
        guards=guards,
        opt=opt,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
    )
    async def delete_user(user_id: Union[UUID, int], service: UserServiceType) -> SQLAUserT:
        """Delete a user from the database."""

        return cast(SQLAUserT, await service.delete_user(user_id))

    return Router(path=path_prefix, route_handlers=[get_user, update_user, delete_user])


def get_role_management_handler(
    path_prefix: str,
    assign_role_path: str,
    revoke_role_path: str,
    guards: list["Guard"],
    identifier_uri: str,
    role_create_dto: type[SQLAlchemyDTO],  # pyright: ignore
    role_read_dto: type[SQLAlchemyDTO],  # pyright: ignore
    role_update_dto: type[SQLAlchemyDTO],  # pyright: ignore
    user_read_dto: type[SQLAlchemyDTO],  # pyright: ignore
    opt: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Router:
    """Get role management route handlers.

    Note:
        Routes are guarded by role authorization.

    Args:
        path_prefix: The path prefix for the routers.
        assign_role_path: The path for the role assignment router.
        revoke_role_path: The path for the role revokement router.
        guards: List of Guard callables to determine who is authorized to manage roles.
        identifier_uri: The path specifying the role ID and its type.
        opt: Optional route handler 'opts' to provide additional context to Guards.
        role_create_dto: A subclass of [RoleCreateDTO][litestar_users.schema.RoleCreateDTO]
        role_read_dto: A subclass of [RoleReadDTO][litestar_users.schema.RoleReadDTO]
        role_update_dto: A subclass of [RoleUpdateDTO][litestar_users.schema.RoleUpdateDTO]
        user_read_dto: A subclass of [UserReadDTO][litestar_users.schema.UserReadDTO]
        tags: A list of string tags to append to the schema of the route handlers.
    """

    @post(
        dto=role_create_dto,
        return_dto=role_read_dto,
        guards=guards,
        opt=opt,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
    )
    async def create_role(data: SQLARoleT, service: UserServiceType) -> SQLARoleT:
        """Create a new role."""
        return cast(SQLARoleT, await service.add_role(data))

    @patch(
        path=identifier_uri,
        dto=role_update_dto,
        return_dto=role_read_dto,
        guards=guards,
        opt=opt,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
    )
    async def update_role(role_id: Union[UUID, int], data: SQLARoleT, service: UserServiceType) -> SQLARoleT:
        """Update a role in the database."""
        data.id = role_id  # type: ignore[assignment]
        return cast(SQLARoleT, await service.update_role(role_id, data))

    @delete(
        path=identifier_uri,
        return_dto=role_read_dto,
        status_code=200,
        guards=guards,
        opt=opt,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
    )
    async def delete_role(role_id: Union[UUID, int], service: UserServiceType) -> SQLARoleT:
        """Delete a role from the database."""

        return cast(SQLARoleT, await service.delete_role(role_id))

    @put(
        return_dto=user_read_dto,
        path=assign_role_path,
        guards=guards,
        opt=opt,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
    )
    async def assign_role(data: UserRoleSchema, service: UserServiceType) -> SQLAUserT:
        """Assign a role to a user."""

        return cast(SQLAUserT, await service.assign_role(data.user_id, data.role_id))

    @put(
        return_dto=user_read_dto,
        path=revoke_role_path,
        guards=guards,
        opt=opt,
        dependencies={"service": Provide(provide_user_service, sync_to_thread=False)},
        tags=tags,
    )
    async def revoke_role(data: UserRoleSchema, service: UserServiceType) -> SQLAUserT:
        """Revoke a role from a user."""

        return cast(SQLAUserT, await service.revoke_role(data.user_id, data.role_id))

    return Router(path_prefix, route_handlers=[create_role, assign_role, revoke_role, update_role, delete_role])
