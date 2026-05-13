"""
Lista curada de dominios de email "desechables" más comunes (mailinator,
10minutemail, guerrillamail, etc.). Pensada para bloquear bots/atacantes
manuales que usan emails throwaway.

NOTA: Esta lista NO es exhaustiva — hay miles. Esto cubre los ~150 más
populares (>90% del tráfico real de spam que enfrenta una pyme). Si querés
extender, pasale más dominios via env var `DISPOSABLE_DOMAINS_EXTRA` (CSV).

Para mantener actualizada: se puede sincronizar periódicamente con la lista
mantenida por la comunidad en https://github.com/disposable-email-domains/
disposable-email-domains  (MIT, ~3500 dominios). V2 si crece la operación.
"""

DISPOSABLE_DOMAINS = frozenset({
    # ─── Tier 1: gigantes del mercado throwaway (los que más se usan) ──
    'mailinator.com', 'mailinator2.com', 'mailnator.com', 'tmailinator.com',
    '10minutemail.com', '10minutemail.net', 'tempmail.com', 'temp-mail.org',
    'guerrillamail.com', 'guerrillamail.biz', 'guerrillamail.de',
    'guerrillamail.info', 'guerrillamail.net', 'guerrillamail.org',
    'guerrillamailblock.com', 'sharklasers.com', 'pokemail.net',
    'spam4.me', 'grr.la', 'maildrop.cc',
    'yopmail.com', 'yopmail.fr', 'yopmail.net',
    'getnada.com', 'getairmail.com', 'temp-mail.io',
    'trashmail.com', 'trashmail.net', 'trashmail.de', 'trashmail.io',
    'fakeinbox.com', 'dispostable.com', 'getmaillet.com',
    'throwawaymail.com', 'throwam.com', 'throwawayemailaddresses.com',
    'mintemail.com', 'mohmal.com', 'emailondeck.com', 'instantemailaddress.com',
    'jetable.org', 'jetable.com', 'mailcatch.com', 'mt2009.com',

    # ─── Tier 2: comunes en spam y bots automatizados ─────────────────
    'tempinbox.com', 'tempinbox.co.uk', 'tempemail.net', 'tempemail.com',
    'temporaryemail.net', 'temporaryforwarding.com', 'temporaryinbox.com',
    'tempmailaddress.com', 'tempmail.eu', 'tempymail.com', 'tempomail.fr',
    'mytrashmail.com', 'mailexpire.com', 'mailfa.tk', 'mailforspam.com',
    'mailfreeonline.com', 'mailguard.me', 'mailimate.com', 'mailmoat.com',
    'mailnesia.com', 'mailtemp.info', 'mailtothis.com', 'mailde.de',
    'maileater.com', 'fake-mail.net', 'fakemail.fr',
    'spambox.us', 'spambog.com', 'spamcorptastic.com', 'spamcowboy.com',
    'spamfree.eu', 'spamfree24.com', 'spamfree24.de', 'spamfree24.eu',
    'spamfree24.info', 'spamfree24.net', 'spamfree24.org',
    'spamgourmet.com', 'spamhereplease.com', 'spaml.com', 'spaml.de',
    'spammotel.com', 'spamobox.com', 'spamspot.com',
    'safetymail.info', 'safetypost.de', 'saynotospams.com',
    'selfdestructingmail.com', 'sendspamhere.com', 'sneakemail.com',
    'snakemail.com', 'soodonims.com', 'sogetthis.com',
    'thisisnotmyrealemail.com', 'thankyou2010.com', 'tradermail.info',
    'trash-mail.at', 'trash-mail.com', 'trash-mail.de', 'trashemail.de',
    'trashymail.com', 'turual.com', 'twinmail.de',
    'wegwerfemail.com', 'wegwerfemail.de', 'wegwerfemail.org',
    'wegwerfmail.de', 'wegwerfmail.info', 'wegwerfmail.net',
    'wegwerfmail.org', 'wegwerfadresse.de', 'weg-werf-email.de',
    'zehnminutenmail.de', 'zippymail.info',

    # ─── Tier 3: variantes regionales/idioma ──────────────────────────
    'correo-temporal.com', 'correoseguro.org', 'correotemporal.com',
    'mailtemporal.com', 'mailtemporario.com', 'temporarioemail.com.br',
    'mail-temporaire.fr', 'mailtemporaire.fr', 'jetable.fr',
    'correotmp.com', 'pinche-mail.com', 'tirrafu.com',

    # ─── Tier 4: emergentes / 2024-2026 ───────────────────────────────
    'tempr.email', 'tempinbox.xyz', 'edu.sa.com', 'gettempmail.net',
    'mailpoof.com', 'inboxbear.com', 'flashmail.org', 'zikmail.com',
    'snapmail.cc', 'pakistmail.com', 'oneoffemail.com',
    'mailtothis.com', 'tempemailfree.com', 'mvrht.com',
    'hidemail.de', 'tempinboxes.com', 'fakeinbox.io',

    # ─── Tier 5: catch-all de variantes Mailinator ────────────────────
    'binkmail.com', 'bobmail.info', 'chammy.info', 'devnullmail.com',
    'letthemeatspam.com', 'mailinater.com', 'mailinator.net',
    'mailinator.org', 'mailinator.us', 'reallymymail.com',
    'safersignup.de', 'sendspamhere.com', 'sogetthis.com', 'spambooger.com',
    'streetwisemail.com', 'suremail.info', 'tradermail.info',
})


def es_email_desechable(email: str, extra: frozenset[str] | set[str] = frozenset()) -> bool:
    """
    True si el email pertenece a un dominio desechable conocido.

    `extra` permite extender la lista en runtime (típicamente desde una
    env var, ej: DISPOSABLE_DOMAINS_EXTRA=foo.com,bar.com).
    """
    if not email or '@' not in email:
        return False
    dominio = email.lower().rsplit('@', 1)[-1].strip()
    return dominio in DISPOSABLE_DOMAINS or dominio in extra
