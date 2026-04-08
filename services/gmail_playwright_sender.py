from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Sequence

from playwright.sync_api import Locator
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from models.client_record import ClientRecord

LogCallback = Callable[[str], None]


class GmailPlaywrightSender:
    DISCARD_NAME_PATTERN = re.compile(r"discard|descartar", re.IGNORECASE)

    COMPOSE_SELECTORS = (
        'div[role="button"][gh="cm"]',
        'div[aria-label="Compose"]',
        'div[aria-label*="Compose"]',
        'div[aria-label="Escrever"]',
        'div[aria-label*="Escrever"]',
    )
    COMPOSE_DIALOG_SELECTOR = 'div[role="dialog"]'
    TO_SELECTORS = (
        'textarea[aria-label*="Para"]',
        'textarea[aria-label*="To"]',
        'textarea[name="to"]',
        'input[name="to"]',
        'input[aria-label*="To"]',
        'input[aria-label*="Para"]',
        'input[aria-label*="Destinat"]',
        'div[aria-label*="To"][contenteditable="true"]',
        'div[aria-label*="Para"][contenteditable="true"]',
        'div[aria-label*="destinat"][contenteditable="true"]',
        'div[role="combobox"][aria-label*="To"]',
        'div[role="combobox"][aria-label*="Para"]',
        'xpath=.//tr[.//span[normalize-space()="Para" or normalize-space()="To"]]//td[2]//div[@contenteditable="true"]',
        'xpath=.//tr[.//span[contains(@aria-label,"Selecionar contatos") or contains(@aria-label,"Select contacts")]]//td[2]//div[@contenteditable="true"]',
        'xpath=.//span[normalize-space()="Para" or normalize-space()="To"]/ancestor::tr[1]//div[@contenteditable="true"]',
    )
    TO_TRIGGER_SELECTORS = (
        'span[aria-label*="Selecionar contatos"]',
        'span[aria-label*="Select contacts"]',
        'span.gO:has-text("Para")',
        'span.gO:has-text("To")',
        'xpath=.//span[normalize-space()="Para" or normalize-space()="To"]',
    )
    SUBJECT_SELECTORS = (
        'input[name="subjectbox"]',
        'input[aria-label*="Subject"]',
        'input[aria-label*="Assunto"]',
    )
    BODY_SELECTORS = (
        'div[aria-label="Message Body"]',
        'div[aria-label*="Message Body"]',
        'div[aria-label*="Corpo"]',
        'div[g_editable="true"][role="textbox"]',
    )
    SEND_SELECTORS = (
        'div[aria-label="Send \\u202a(Ctrl-Enter)\\u202c"]',
        'div[aria-label*="Send"]',
        'div[aria-label*="Enviar"]',
        'div[aria-label^="Send"]',
        'div[aria-label^="Enviar"]',
        'div[data-tooltip^="Send"]',
        'div[data-tooltip^="Enviar"]',
        'div[role="button"][data-tooltip^="Send"]',
        'div[role="button"][data-tooltip^="Enviar"]',
        'button[aria-label*="Send"]',
        'button[aria-label*="Enviar"]',
    )

    def __init__(self, user_data_dir: str, headless: bool = False) -> None:
        profile_dir = Path(user_data_dir).expanduser().resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)

        self.user_data_dir = str(profile_dir)
        self.headless = headless

    def open_gmail_for_manual_login(self, timeout_ms: int = 300000) -> bool:
        return self._open_and_check_session(timeout_ms=timeout_ms)

    def validate_session(self, timeout_ms: int = 15000) -> bool:
        return self._open_and_check_session(timeout_ms=timeout_ms)

    def send_batch(
        self,
        recipients: Sequence[ClientRecord],
        subject: str,
        body: str,
        log_callback: LogCallback | None = None,
    ) -> dict[str, object]:
        ok_count = 0
        error_count = 0
        results: list[dict[str, object]] = []

        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://mail.google.com/", wait_until="domcontentloaded")

                if not self._wait_for_compose(page, timeout_ms=20000):
                    raise RuntimeError("Sessao Gmail invalida. Faca login na Tela 1.")

                for record in recipients:
                    email = (record.email or "").strip()
                    if not email:
                        error_count += 1
                        error_message = "Registro sem email valido"
                        self._log(log_callback, f"ERRO | {record.id} | {email} | {error_message}")
                        results.append(
                            {
                                "id": record.id,
                                "email": email,
                                "ok": False,
                                "error": error_message,
                            }
                        )
                        continue

                    try:
                        self._send_single(page, email, subject, body)
                        ok_count += 1
                        self._log(log_callback, f"OK | {record.id} | {email}")
                        results.append(
                            {
                                "id": record.id,
                                "email": email,
                                "ok": True,
                                "error": "",
                            }
                        )
                    except Exception as error:  # noqa: BLE001
                        error_count += 1
                        error_message = str(error)
                        self._log(log_callback, f"ERRO | {record.id} | {email} | {error_message}")
                        results.append(
                            {
                                "id": record.id,
                                "email": email,
                                "ok": False,
                                "error": error_message,
                            }
                        )
                        self._dismiss_compose_if_open(page)

                return {"ok": ok_count, "error": error_count, "results": results}
            finally:
                context.close()

    def send_batch_composed(
        self,
        items: Sequence[tuple[ClientRecord, str, str]],
        log_callback: LogCallback | None = None,
    ) -> dict[str, object]:
        ok_count = 0
        error_count = 0
        results: list[dict[str, object]] = []

        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://mail.google.com/", wait_until="domcontentloaded")

                if not self._wait_for_compose(page, timeout_ms=20000):
                    raise RuntimeError("Sessao Gmail invalida. Faca login na Tela 1.")

                for record, subject_final, body_final in items:
                    email = (record.email or "").strip()
                    if not email:
                        error_count += 1
                        error_message = "Registro sem email valido"
                        self._log(log_callback, f"ERRO | {record.id} | {email} | {error_message}")
                        results.append(
                            {
                                "id": record.id,
                                "email": email,
                                "ok": False,
                                "error": error_message,
                            }
                        )
                        continue

                    try:
                        self._send_single(page, email, subject_final, body_final)
                        ok_count += 1
                        self._log(log_callback, f"OK | {record.id} | {email}")
                        results.append(
                            {
                                "id": record.id,
                                "email": email,
                                "ok": True,
                                "error": "",
                            }
                        )
                    except Exception as error:  # noqa: BLE001
                        error_count += 1
                        error_message = str(error)
                        self._log(log_callback, f"ERRO | {record.id} | {email} | {error_message}")
                        results.append(
                            {
                                "id": record.id,
                                "email": email,
                                "ok": False,
                                "error": error_message,
                            }
                        )
                        self._dismiss_compose_if_open(page)

                return {"ok": ok_count, "error": error_count, "results": results}
            finally:
                context.close()

    def _open_and_check_session(self, timeout_ms: int) -> bool:
        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://mail.google.com/", wait_until="domcontentloaded")
                return self._wait_for_compose(page, timeout_ms=timeout_ms)
            finally:
                context.close()

    def _launch_context(self, playwright) -> object:
        return playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            no_viewport=True,
            args=["--start-maximized"],
        )

    def _wait_for_compose(self, page: Page, timeout_ms: int) -> bool:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            for selector in self.COMPOSE_SELECTORS:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                try:
                    if locator.is_visible():
                        return True
                except Exception:
                    continue
            page.wait_for_timeout(250)
        return False

    def _send_single(self, page: Page, email: str, subject: str, body: str) -> None:
        self._click_compose(page)
        page.wait_for_timeout(1000)
        compose_dialog = self._wait_for_compose_dialog(page)

        self._fill_recipient(compose_dialog, page, email)
        self._assert_any_recipient(compose_dialog)
        self._fill_first_existing(compose_dialog, self.SUBJECT_SELECTORS, subject, "campo Subject")
        self._fill_first_existing(compose_dialog, self.BODY_SELECTORS, body, "campo Body")
        self._send_compose(compose_dialog, page)

        page.wait_for_timeout(1200)

    def _click_compose(self, page: Page) -> None:
        for selector in self.COMPOSE_SELECTORS:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue

            try:
                locator.click(timeout=2500)
                return
            except Exception:
                continue

        raise RuntimeError("Nao foi possivel localizar botao Compose/Escrever.")

    def _wait_for_compose_dialog(self, page: Page) -> Locator:
        dialog = page.locator(self.COMPOSE_DIALOG_SELECTOR).last
        dialog.wait_for(state="visible", timeout=10000)
        return dialog

    def _fill_recipient(self, scope: Locator, page: Page, email: str) -> None:
        recipients = self._split_recipients(email)
        if not recipients:
            raise RuntimeError("Campo To vazio.")

        to_target = self._resolve_to_target(scope, page)
        if to_target is None:
            raise RuntimeError(
                "Nao foi possivel encontrar campo To. "
                f"Textboxes detectados: {self._describe_textboxes(scope)}"
            )

        for recipient in recipients:
            if not self._type_recipient_without_reclick(to_target, scope, page, recipient):
                raise RuntimeError(f"Falha ao preencher campo To ({recipient}).")

    def _resolve_to_target(self, scope: Locator, page: Page) -> Locator | None:
        direct_target = self._find_to_target(scope)
        if direct_target is not None and self._focus_target_once(direct_target):
            return direct_target

        for trigger_selector in self.TO_TRIGGER_SELECTORS:
            trigger = scope.locator(trigger_selector).first
            if trigger.count() == 0:
                continue

            try:
                trigger.click(timeout=2000)
                page.wait_for_timeout(200)
            except Exception:
                continue

            activated_target = self._find_to_target(scope)
            if activated_target is not None and self._focus_target_once(activated_target):
                return activated_target

        return None

    def _find_to_target(self, scope: Locator) -> Locator | None:
        for selector in self.TO_SELECTORS:
            locator = scope.locator(selector).first
            if locator.count() == 0:
                continue
            if self._looks_like_to_field(locator):
                return locator

        return None

    def _focus_target_once(self, target: Locator) -> bool:
        try:
            target.click(timeout=2000)
            return True
        except Exception:
            try:
                target.focus()
                return True
            except Exception:
                return False

    def _type_recipient_without_reclick(self, target: Locator, scope: Locator, page: Page, email: str) -> bool:
        if not self._looks_like_to_field(target):
            return False

        try:
            target.type(email, delay=20)
        except Exception:
            try:
                page.keyboard.type(email, delay=20)
            except Exception:
                return False

        try:
            page.keyboard.press("Enter")
        except Exception:
            return False

        if self._wait_recipient_added(scope, page, email, timeout_ms=3000):
            return True

        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

        return self._wait_recipient_added(scope, page, email, timeout_ms=1500)

    def _split_recipients(self, email_field_value: str) -> list[str]:
        tokens = [token.strip() for token in re.split(r"[,;\n]+", email_field_value) if token.strip()]
        return tokens

    def _looks_like_to_field(self, target: Locator) -> bool:
        try:
            aria_label = (target.get_attribute("aria-label") or "").lower()
            name_attr = (target.get_attribute("name") or "").lower()
            role_attr = (target.get_attribute("role") or "").lower()
            contenteditable = (target.get_attribute("contenteditable") or "").lower()

            if name_attr == "to":
                return True
            if any(token in aria_label for token in ("to", "para", "destinat", "recipient")):
                return True
            if role_attr == "combobox" and any(token in aria_label for token in ("to", "para", "destinat")):
                return True
            if contenteditable == "true" and self._is_inside_recipient_row(target):
                return True

            if any(token in aria_label for token in ("message body", "corpo da mensagem", "assunto", "subject")):
                return False
        except Exception:
            return False

        return False

    def _is_inside_recipient_row(self, target: Locator) -> bool:
        try:
            return bool(
                target.evaluate(
                    """
                    (el) => {
                        const row = el.closest('tr');
                        if (!row) return false;
                        const text = (row.innerText || '').toLowerCase();
                        return text.includes('para') || text.includes('to') || text.includes('destinat');
                    }
                    """
                )
            )
        except Exception:
            return False

    def _wait_recipient_added(self, scope: Locator, page: Page, email: str, timeout_ms: int) -> bool:
        checks = max(1, timeout_ms // 250)
        for _ in range(checks):
            recipient_badges = scope.locator(
                f'[email="{email}"], [data-hovercard-id="{email}"], '
                f'span[email="{email}"], span[data-hovercard-id="{email}"], '
                f'div[role="button"][email="{email}"]'
            )
            if recipient_badges.count() > 0:
                return True

            to_field_values = scope.locator(
                f'textarea[name="to"][value*="{email}"], '
                f'input[name="to"][value*="{email}"], '
                f'textarea[aria-label*="Para"][value*="{email}"], '
                f'textarea[aria-label*="To"][value*="{email}"], '
                f'input[aria-label*="Para"][value*="{email}"], '
                f'input[aria-label*="To"][value*="{email}"]'
            )
            if to_field_values.count() > 0:
                return True

            page.wait_for_timeout(250)
        return False

    def _assert_any_recipient(self, scope: Locator) -> None:
        recipient_any = scope.locator('[email], [data-hovercard-id], span[email], span[data-hovercard-id]')
        if recipient_any.count() > 0:
            return

        raise RuntimeError("Adicione pelo menos um destinatario no campo Para antes de enviar.")

    def _describe_textboxes(self, scope: Locator) -> str:
        try:
            textboxes = scope.get_by_role("textbox")
            count = textboxes.count()
            if count == 0:
                return "nenhum"

            details: list[str] = []
            for index in range(min(count, 8)):
                textbox = textboxes.nth(index)
                aria_label = textbox.get_attribute("aria-label") or ""
                name = textbox.get_attribute("name") or ""
                data_tooltip = textbox.get_attribute("data-tooltip") or ""
                details.append(
                    f"#{index + 1}(aria-label='{aria_label}', name='{name}', tooltip='{data_tooltip}')"
                )
            return "; ".join(details)
        except Exception:
            return "indisponivel"

    def _fill_first_existing(
        self,
        scope: Page | Locator,
        selectors: tuple[str, ...],
        value: str,
        label: str,
    ) -> None:
        last_error: Exception | None = None

        for selector in selectors:
            locator = scope.locator(selector).first
            if locator.count() == 0:
                continue

            try:
                locator.click(timeout=2500)
                locator.fill(value)
                return
            except Exception as error:  # noqa: BLE001
                last_error = error

        if last_error is not None:
            raise RuntimeError(f"Falha ao preencher {label}: {last_error}") from last_error
        raise RuntimeError(f"Nao foi possivel encontrar {label}.")

    def _try_click_first_existing(self, scope: Page | Locator, selectors: tuple[str, ...]) -> bool:
        for selector in selectors:
            locator = scope.locator(selector).first
            if locator.count() == 0:
                continue

            try:
                locator.click(timeout=2500)
                return True
            except Exception:
                continue

        return False

    def _send_compose(self, compose_dialog: Locator, page: Page) -> None:
        clicked = self._try_click_first_existing(compose_dialog, self.SEND_SELECTORS)

        if not clicked:
            page.keyboard.press("Control+Enter")

        if self._wait_compose_closed(compose_dialog, timeout_ms=7000):
            return

        page.keyboard.press("Control+Enter")

        if self._wait_compose_closed(compose_dialog, timeout_ms=7000):
            return

        raise RuntimeError("Nao foi possivel enviar email. O modal de composicao permaneceu aberto.")

    def _wait_compose_closed(self, compose_dialog: Locator, timeout_ms: int) -> bool:
        try:
            compose_dialog.wait_for(state="hidden", timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            return False

    def _dismiss_compose_if_open(self, page: Page) -> None:
        try:
            dialog = page.locator(self.COMPOSE_DIALOG_SELECTOR).last
            if dialog.count() == 0:
                return

            if not dialog.is_visible():
                return

            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            discard_button = page.get_by_role("button", name=self.DISCARD_NAME_PATTERN).first
            if discard_button.count() > 0 and discard_button.is_visible():
                discard_button.click(timeout=1500)
        except Exception:
            return

    def _log(self, callback: LogCallback | None, message: str) -> None:
        if callback:
            callback(message)
