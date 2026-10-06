"""Модуль tgbot: обработчики aiogram и отправка — без сети, Telegram и PostgreSQL.

Обработчики вызываются напрямую с фейковыми Message/CallbackQuery (duck-typing — aiogram
не проверяет типы в рантайме), сессия — FakeSession со сценарием ответов в порядке
запросов. Проверяются привязка по одноразовому коду и по invite-токену HR, дайджест,
принятие приглашения с маскированием контактов в журнале и дедупликация пушей.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from aiogram.filters import CommandObject

from app.models.employer import Company, CompanyMember, Invitation
from app.models.tgbot import TgLink, TgLinkToken, TgOutboxMessage
from app.modules.matching import service as matching_service
from app.modules.tgbot import digest as digest_module
from app.modules.tgbot import handlers, scheduler
from app.modules.tgbot import service as tg_service
from tests.conftest import make_user
from tests.fake_session import FakeSession

CHAT_ID = 100_500


class SessionFactory:
    """`session_factory()` → async context manager поверх FakeSession."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def __call__(self) -> SessionFactory:
        return self

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class FakeMessage:
    def __init__(self, chat_id: int = CHAT_ID) -> None:
        self.chat = FakeChat(chat_id)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: object) -> None:
        self.answers.append(text)


class FakeCallback:
    def __init__(self, data: str, chat_id: int = CHAT_ID) -> None:
        self.data = data
        self.message = FakeMessage(chat_id)
        self.alerts: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        self.alerts.append((text, show_alert))


def link_row(role: str = "candidate", user_id: uuid.UUID | None = None) -> TgLink:
    return TgLink(
        id=uuid.uuid4(),
        tg_chat_id=CHAT_ID,
        user_id=user_id or uuid.uuid4(),
        member_id=None,
        role=role,
    )


class TestStart:
    async def test_without_args_shows_help(self) -> None:
        message = FakeMessage()
        await handlers.on_start(
            message,  # type: ignore[arg-type]
            CommandObject(prefix="/", command="start", args=None),
            SessionFactory(FakeSession()),  # type: ignore[arg-type]
        )
        assert "ФСП.Орбита" in message.answers[0]

    async def test_link_code_binds_candidate(self) -> None:
        user_id = uuid.uuid4()
        token_row = TgLinkToken(
            id=uuid.uuid4(),
            user_id=user_id,
            role="candidate",
            token_hash="x" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            used_at=None,
        )
        # consume_link_token → find_link (None → создаётся привязка)
        session = FakeSession(scalars=[token_row, None])
        message = FakeMessage()
        await handlers.on_start(
            message,  # type: ignore[arg-type]
            CommandObject(prefix="/", command="start", args="secret-code"),
            SessionFactory(session),  # type: ignore[arg-type]
        )
        assert token_row.used_at is not None, "код одноразовый"
        link = session.added_of(TgLink)[0]
        assert link.tg_chat_id == CHAT_ID
        assert link.user_id == user_id and link.role == "candidate"
        assert "кандидата" in message.answers[0]

    async def test_hr_invite_token_binds_member_without_account(self) -> None:
        company = Company(id=uuid.uuid4(), user_id=uuid.uuid4(), name="ФСП.Орбита Демо")
        member = CompanyMember(
            id=uuid.uuid4(),
            company_id=company.id,
            user_id=None,
            role="hr",
            status="invited",
            invite_token_hash="y" * 64,
        )
        # link-код не найден → участник по invite-токену → привязка чата
        session = FakeSession(scalars=[None, member, None], rows={(Company, company.id): company})
        message = FakeMessage()
        await handlers.on_start(
            message,  # type: ignore[arg-type]
            CommandObject(prefix="/", command="start", args="demo-orbita-hr-invite-2026"),
            SessionFactory(session),  # type: ignore[arg-type]
        )
        link = session.added_of(TgLink)[0]
        assert link.member_id == member.id and link.user_id is None
        assert link.role == "employer"
        # company_member не мутируется: токен остаётся действительным для веба
        assert member.status == "invited" and member.invite_token_hash == "y" * 64
        assert "ФСП.Орбита Демо" in message.answers[0]

    async def test_unknown_code(self) -> None:
        session = FakeSession(scalars=[None, None])
        message = FakeMessage()
        await handlers.on_start(
            message,  # type: ignore[arg-type]
            CommandObject(prefix="/", command="start", args="nope"),
            SessionFactory(session),  # type: ignore[arg-type]
        )
        assert "не найден" in message.answers[0]


class TestDigestCommand:
    async def test_not_linked(self) -> None:
        message = FakeMessage()
        session = FakeSession(scalars=[None])
        await handlers.on_digest(message, SessionFactory(session))  # type: ignore[arg-type]
        assert "не привязан" in message.answers[0]

    async def test_candidate_digest(self, monkeypatch) -> None:
        link = link_row(role="candidate")
        profile = object()

        async def fake_build(session, role, subject):
            return "ФСП.Орбита — ваш дайджест (тест)"

        monkeypatch.setattr(digest_module, "build_digest_for_link", fake_build)
        # find_link → профиль кандидата; deliver пишет журнал tg_outbox
        session = FakeSession(scalars=[link, profile])
        message = FakeMessage()
        await handlers.on_digest(message, SessionFactory(session))  # type: ignore[arg-type]
        assert message.answers == ["ФСП.Орбита — ваш дайджест (тест)"]
        outbox = session.added_of(TgOutboxMessage)[0]
        assert outbox.kind == tg_service.KIND_MANUAL_DIGEST
        assert outbox.tg_chat_id == CHAT_ID


class TestInvitationCallbacks:
    async def test_accept_shows_contacts_and_masks_journal(self, monkeypatch) -> None:
        user = make_user(roles=["candidate"])
        link = link_row(role="candidate", user_id=user.id)
        invitation = Invitation(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
            title="Backend middle",
            salary_from=180_000,
            salary_to=260_000,
            contact_method="telegram",
            status="accepted",
        )
        company = Company(
            id=invitation.company_id,
            user_id=uuid.uuid4(),
            name="ФСП.Орбита Демо",
            contact_email="hr@fsp.local",
            contact_phone="+7 916 000-11-22",
        )

        async def fake_accept(session, actor, invitation_id, ip=None):
            assert invitation_id == invitation.id
            return invitation, company

        monkeypatch.setattr(matching_service, "accept_invitation", fake_accept)
        session = FakeSession(scalars=[link], rows={(type(user), user.id): user})
        callback = FakeCallback(f"a:{tg_service.pack_uuid(invitation.id)}")
        await handlers.on_invitation_accept(
            callback,  # type: ignore[arg-type]
            SessionFactory(session),  # type: ignore[arg-type]
        )
        # кандидату в чат — полные контакты компании
        reply = callback.message.answers[0]
        assert "hr@fsp.local" in reply and "ФСП.Орбита Демо" in reply
        # в журнале tg_outbox — только маскированные
        outbox = session.added_of(TgOutboxMessage)[0]
        assert "hr@fsp.local" not in outbox.text
        assert "h***r@fsp.local" not in outbox.text  # короткий local маскируется как h***
        assert "@fsp.local" in outbox.text and "***" in outbox.text
        assert callback.alerts[0][0] == "Приглашение принято"

    async def test_decline(self, monkeypatch) -> None:
        user = make_user(roles=["candidate"])
        link = link_row(role="candidate", user_id=user.id)
        declined: list[uuid.UUID] = []

        async def fake_decline(session, actor, invitation_id, reason_code, text, ip=None):
            declined.append(invitation_id)
            return None

        monkeypatch.setattr(matching_service, "decline_invitation", fake_decline)
        session = FakeSession(scalars=[link], rows={(type(user), user.id): user})
        invitation_id = uuid.uuid4()
        callback = FakeCallback(f"d:{tg_service.pack_uuid(invitation_id)}")
        await handlers.on_invitation_decline(
            callback,  # type: ignore[arg-type]
            SessionFactory(session),  # type: ignore[arg-type]
        )
        assert declined == [invitation_id]
        assert callback.alerts[0][0] == "Приглашение отклонено"

    async def test_accept_requires_candidate_link(self) -> None:
        session = FakeSession(scalars=[None])  # привязки нет
        callback = FakeCallback(f"a:{tg_service.pack_uuid(uuid.uuid4())}")
        await handlers.on_invitation_accept(
            callback,  # type: ignore[arg-type]
            SessionFactory(session),  # type: ignore[arg-type]
        )
        assert callback.alerts[0] == ("Чат не привязан к профилю кандидата", True)


class TestInvitationPushes:
    async def test_push_once_per_invitation(self) -> None:
        profile_id, user_id = uuid.uuid4(), uuid.uuid4()
        invitation = Invitation(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            candidate_id=profile_id,
            title="Backend middle",
            salary_from=180_000,
            salary_to=260_000,
            status="sent",
            sent_at=datetime.now(UTC),
            explanation={"text": "Закрывает 6 из 7 узлов"},
        )

        class FakeProfile:
            id = profile_id

            def __init__(self) -> None:
                self.user_id = user_id

        link = TgLink(
            id=uuid.uuid4(), tg_chat_id=CHAT_ID, user_id=user_id, member_id=None, role="candidate"
        )
        company = Company(id=invitation.company_id, user_id=uuid.uuid4(), name="ФСП.Орбита Демо")
        # приглашения → профили → привязки → дедупликация (пусто = ещё не слали)
        session = FakeSession(
            scalars=[[invitation], [FakeProfile()], [link], None],
            rows={(Company, company.id): company},
        )
        sent = await scheduler.push_new_invitations(session, None)  # type: ignore[arg-type]
        assert sent == 1
        outbox = session.added_of(TgOutboxMessage)[0]
        assert outbox.kind == tg_service.KIND_INVITATION_PUSH
        assert outbox.ref_id == invitation.id
        assert outbox.buttons and outbox.buttons[0][0]["text"] == "Принять"
        assert "180–260 тыс. ₽" in outbox.text

    async def test_push_deduplicated(self) -> None:
        profile_id, user_id = uuid.uuid4(), uuid.uuid4()
        invitation = Invitation(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            candidate_id=profile_id,
            title="Backend middle",
            salary_from=180_000,
            salary_to=260_000,
            status="sent",
            sent_at=datetime.now(UTC),
        )

        class FakeProfile:
            id = profile_id

            def __init__(self) -> None:
                self.user_id = user_id

        link = TgLink(
            id=uuid.uuid4(), tg_chat_id=CHAT_ID, user_id=user_id, member_id=None, role="candidate"
        )
        # дедупликация находит прежнюю запись журнала → пуш не повторяется
        session = FakeSession(scalars=[[invitation], [FakeProfile()], [link], 1])
        sent = await scheduler.push_new_invitations(session, None)  # type: ignore[arg-type]
        assert sent == 0
        assert session.added_of(TgOutboxMessage) == []


class TestDeliverSanitizer:
    async def test_outgoing_text_is_sanitized(self) -> None:
        session = FakeSession()
        await tg_service.deliver(
            session,  # type: ignore[arg-type]
            None,
            tg_service.OutgoingMessage(
                tg_chat_id=CHAT_ID,
                text="Напишите кандидату на ivanov@example.com или +7 916 123-45-67",
            ),
        )
        outbox = session.added_of(TgOutboxMessage)[0]
        assert "ivanov@example.com" not in outbox.text
        assert "123-45-67" not in outbox.text
        assert outbox.status == "stub"
