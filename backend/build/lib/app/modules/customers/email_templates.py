"""Email templates for customer-facing emails (portal credentials, etc.)."""

# Logo URL for email header (hosted on your domain; change if needed)
EMAIL_LOGO_URL = "https://innexar.com.br/logo.png"


def portal_credentials_email(
    login_url: str,
    recipient_email: str,
    temporary_password: str,
    *,
    after_payment: bool = False,
    briefing_url: str | None = None,
) -> tuple[str, str, str]:
    """Return (subject, body_plain, body_html) for portal access email.
    Subject is short and professional (no URL or password in subject)."""
    subject = "Innexar – Seus dados de acesso ao portal do cliente"
    body_plain = (
        "Olá,\n\n"
        + ("Seu pagamento foi aprovado. " if after_payment else "")
        + "Segue seu acesso ao portal da Innexar:\n\n"
        f"Acesse: {login_url}\n"
        f"E-mail: {recipient_email}\n"
        f"Senha temporária: {temporary_password}\n\n"
        "Recomendamos alterar a senha após o primeiro acesso.\n\n"
    )
    if after_payment and briefing_url:
        body_plain += f"Próximo passo: preencha os dados do seu site em {briefing_url}\n\n"
    body_plain += "— Equipe Innexar"
    body_html = _portal_credentials_html(
        login_url=login_url,
        recipient_email=recipient_email,
        temporary_password=temporary_password,
        after_payment=after_payment,
        briefing_url=briefing_url,
    )
    return subject, body_plain, body_html


def _email_header_html(logo_url: str) -> str:
    """Professional header with logo for transactional emails."""
    return f"""
    <tr>
      <td style="padding: 28px 28px 24px; background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%); text-align: center;">
        <a href="https://innexar.com.br" style="text-decoration: none;">
          <img src="{logo_url}" alt="Innexar" width="140" height="40" style="display: inline-block; max-width: 140px; height: auto; border: 0;" />
        </a>
      </td>
    </tr>"""


def _email_footer_html() -> str:
    """Professional footer: signature, company info, legal."""
    return """
    <tr>
      <td style="padding: 24px 28px; background: #f8fafc; border-top: 1px solid #e2e8f0;">
        <p style="margin:0 0 8px; font-size: 14px; font-weight: 600; color: #0f172a;">Equipe Innexar</p>
        <p style="margin:0 0 4px; font-size: 13px; color: #475569;">
          <a href="https://innexar.com.br" style="color: #2563eb; text-decoration: none;">innexar.com.br</a>
        </p>
        <p style="margin: 12px 0 0; font-size: 11px; color: #94a3b8; line-height: 1.4;">
          Este e-mail foi enviado porque você é cliente ou solicitou acesso ao portal. Em caso de dúvidas, responda a esta mensagem.
        </p>
      </td>
    </tr>
    <tr>
      <td style="padding: 12px 28px; background: #f1f5f9; text-align: center;">
        <p style="margin:0; font-size: 11px; color: #94a3b8;">© Innexar. Todos os direitos reservados.</p>
      </td>
    </tr>"""


def _portal_credentials_html(
    login_url: str,
    recipient_email: str,
    temporary_password: str,
    after_payment: bool,
    briefing_url: str | None = None,
) -> str:
    intro = (
        "Seu pagamento foi aprovado. Segue seu acesso ao portal:"
        if after_payment
        else "Segue seu acesso ao portal da Innexar:"
    )
    header = _email_header_html(EMAIL_LOGO_URL)
    footer = _email_footer_html()
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Acesso ao portal</title>
</head>
<body style="margin:0; padding:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f1f5f9; color: #1e293b;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9;">
    <tr>
      <td style="padding: 32px 16px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); overflow: hidden;">
          {header}
          <tr>
            <td style="padding: 28px;">
              <p style="margin:0 0 16px; font-size: 15px; line-height: 1.5; color: #475569;">Olá,</p>
              <p style="margin:0 0 24px; font-size: 15px; line-height: 1.5; color: #475569;">{intro}</p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background: #f8fafc; border-radius: 8px; border: 1px solid #e2e8f0;">
                <tr>
                  <td style="padding: 20px;">
                    <p style="margin:0 0 12px; font-size: 13px; color: #64748b;">Acesse o portal</p>
                    <p style="margin:0 0 16px; font-size: 15px;"><a href="{login_url}" style="color: #2563eb; text-decoration: none; font-weight: 500;">{login_url}</a></p>
                    <p style="margin:0 0 8px; font-size: 13px; color: #64748b;">E-mail</p>
                    <p style="margin:0 0 16px; font-size: 15px; color: #1e293b;">{recipient_email}</p>
                    <p style="margin:0 0 8px; font-size: 13px; color: #64748b;">Senha temporária</p>
                    <p style="margin:0; font-size: 15px; font-family: ui-monospace, monospace; color: #1e293b; letter-spacing: 0.02em;">{temporary_password}</p>
                  </td>
                </tr>
              </table>
              <p style="margin: 20px 0 0; font-size: 14px; color: #64748b;">Recomendamos alterar a senha após o primeiro acesso.</p>
              <p style="margin: 28px 0 0;">
                <a href="{login_url}" style="display: inline-block; padding: 12px 24px; background: #2563eb; color: #ffffff; text-decoration: none; font-size: 14px; font-weight: 500; border-radius: 8px;">Acessar portal</a>
              </p>
              """ + (
                f'<p style="margin: 24px 0 0; font-size: 14px; color: #475569;">Próximo passo: <a href="{briefing_url}" style="color: #2563eb;">preencha os dados do seu site</a> (nome, serviços, fotos) para começarmos a construção.</p>'
                if briefing_url else ""
              ) + """
            </td>
          </tr>
          """ + footer + """
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
