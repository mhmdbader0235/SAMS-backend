import logging
import os
import smtplib
import asyncio
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _send_email_sync(to_email: str, invite_code: str, role: str) -> None:
    """Synchronous SMTP email delivery helper."""
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    registration_link = f"{frontend_url}/auth?invite_code={invite_code}&auto_google=true&email={to_email}"
    
    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; background-color: #0f172a; color: #f1f5f9; border-radius: 16px; border: 1px solid rgba(255, 255, 255, 0.1);">
        <div style="text-align: center; margin-bottom: 24px;">
            <h1 style="font-size: 24px; font-weight: 800; color: #10b981; margin: 0;">SchoolDesk</h1>
            <p style="font-size: 14px; color: #94a3b8; margin-top: 4px;">School Event & Workspace Management</p>
        </div>
        <div style="background-color: rgba(30, 41, 59, 0.7); padding: 20px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.05); margin-bottom: 24px;">
            <h2 style="font-size: 18px; font-weight: 700; color: #f8fafc; margin-top: 0;">You're Invited!</h2>
            <p style="font-size: 14px; color: #cbd5e1; line-height: 1.6;">You have been invited to join the SchoolDesk workspace as a <strong>{role.replace('_', ' ').title()}</strong>.</p>
            <p style="font-size: 14px; color: #cbd5e1; line-height: 1.6;">Click the button below to complete registration and log in directly via Google SSO:</p>
            <div style="text-align: center; margin: 28px 0;">
                <a href="{registration_link}" style="background-color: #059669; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 10px; font-weight: 700; font-size: 14px; display: inline-block; box-shadow: 0 4px 14px rgba(5, 150, 105, 0.4);">Complete Registration</a>
            </div>
            <p style="font-size: 12px; color: #94a3b8; text-align: center;">Invitation Code: <strong style="color: #34d399;">{invite_code}</strong></p>
        </div>
        <p style="font-size: 12px; color: #64748b; text-align: center; margin: 0;">If you did not request this invitation, you may safely ignore this email.</p>
    </div>
    """
    
    gmail_user = os.getenv("GMAIL_SMTP_USER")
    gmail_password = os.getenv("GMAIL_SMTP_PASSWORD")
    
    if gmail_user and gmail_password:
        msg = EmailMessage()
        msg['Subject'] = "You're invited to SchoolDesk!"
        msg['From'] = f"SchoolDesk <{gmail_user}>"
        msg['To'] = to_email
        msg.set_content("Please enable HTML to view this invitation.")
        msg.add_alternative(html_body, subtype='html')
        
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(gmail_user, gmail_password)
                smtp.send_message(msg)
            logger.info(f"Successfully sent invitation email to {to_email} via Gmail SMTP.")
        except Exception as e:
            logger.error(f"Failed to send email via Gmail to {to_email}: {e}")
    else:
        logger.info("GMAIL_SMTP_USER not configured. Logging mock invitation email link.")
        print(f"\n========== INVITATION EMAIL (MOCK) ==========\nTo: {to_email}\nLink: {registration_link}\n=============================================\n")


async def send_invitation_email(to_email: str, invite_code: str, role: str) -> None:
    """Sends an invitation email using SMTP asynchronously without blocking the event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send_email_sync, to_email, invite_code, role)

