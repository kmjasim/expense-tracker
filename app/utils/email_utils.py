from flask_mail import Message
from flask import current_app, render_template
from ..extensions import mail
# app/utils/mailer.py
import smtplib
from email.message import EmailMessage

def send_mail(to_email: str, subject: str, html_body: str, text_body: str | None = None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = current_app.config["MAIL_DEFAULT_SENDER"][1]
    msg["To"] = to_email
    msg.set_content(text_body or "See HTML version.")
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(current_app.config["MAIL_SERVER"], current_app.config["MAIL_PORT"]) as smtp:
        if current_app.config.get("MAIL_USE_TLS"):
            smtp.starttls()
        smtp.login(current_app.config["MAIL_USERNAME"], current_app.config["MAIL_PASSWORD"])
        smtp.send_message(msg)
