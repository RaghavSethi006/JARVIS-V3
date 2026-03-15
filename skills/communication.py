from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger
import asyncio
import email
from email.header import decode_header
import imaplib
import os

class CommunicationSkill(BaseSkill):
    def register(self):
        self.bus.subscribe("send_email", self.handle_send_email)
        self.bus.subscribe("read_emails", self.handle_read_emails)

    async def handle_send_email(self, event: Event):
        receiver = (event.data or {}).get("receiver")
        subject = (event.data or {}).get("subject")
        body = (event.data or {}).get("body")
        if not all([receiver, subject, body]):
            await self.bus.emit("tts_speak", "Please provide recipient, subject, and message.")
            return
        await self.bus.emit("tts_speak", f"Sending email to {receiver}...")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._send_email_sync, receiver, subject, body)
            await self.bus.emit("tts_speak", "Email sent successfully.")
        except Exception as e:
            logger.error(f"Email error: {e}")
            await self.bus.emit("tts_speak", f"Failed to send email: {str(e)}")

    def _send_email_sync(self, receiver: str, subject: str, body: str):
        """Synchronous - runs in executor thread."""
        import smtplib
        from email.mime.text import MIMEText

        sender = os.environ.get("EMAIL_ADDRESS", "")
        password = os.environ.get("EMAIL_PASSWORD", "")
        if not sender or not password:
            raise ValueError("EMAIL_ADDRESS or EMAIL_PASSWORD not set in .env")

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = receiver

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())

    async def handle_read_emails(self, event: Event):
        count = int((event.data or {}).get("count", 5))
        email_addr = os.environ.get("EMAIL_ADDRESS", "")
        password = os.environ.get("EMAIL_PASSWORD", "")
        imap_host = os.environ.get("IMAP_HOST", "imap.gmail.com")

        if not email_addr or not password:
            await self.bus.emit("tts_speak", "Email credentials not configured.")
            return

        loop = asyncio.get_event_loop()
        summaries = await loop.run_in_executor(
            None,
            self._fetch_emails,
            email_addr,
            password,
            imap_host,
            count,
        )
        if not summaries:
            await self.bus.emit("tts_speak", "No unread emails.")
            return
        await self.bus.emit("tts_speak", f"You have {len(summaries)} unread emails.")
        for summary in summaries[:3]:
            await self.bus.emit(
                "tts_speak",
                f"From {summary['from']}. Subject: {summary['subject']}.",
            )

    def _fetch_emails(self, addr, pwd, host, count) -> list[dict]:
        try:
            mail = imaplib.IMAP4_SSL(host)
            mail.login(addr, pwd)
            mail.select("inbox")
            _, data = mail.search(None, "UNSEEN")
            ids = data[0].split()[-count:]
            results = []
            for num in reversed(ids):
                _, msg_data = mail.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject, enc = decode_header(msg["Subject"])[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(enc or "utf-8", errors="replace")
                sender = msg.get("From", "Unknown")
                results.append({"from": sender, "subject": subject})
            mail.logout()
            return results
        except Exception as exc:
            logger.error("IMAP error: %s", exc)
            return []
