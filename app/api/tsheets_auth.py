"""
QuickBooks Time (T-Sheets) authentication.

An earlier version of this file assumed QuickBooks Time went through
Intuit's unified OAuth platform (developer.intuit.com / oauth.platform.
intuit.com) - the same system used for QuickBooks Online. That assumption
was wrong, and cost a round of real trial and error to find: creating an
app there and requesting the "com.intuit.quickbooks.timetracking" scope
gets rejected live with "The scope query parameter value is invalid",
and Intuit's own developer support forum confirms why - "Currently there
is no supported method to create a new QuickBooks Time OAuth application
through the intuit developer portal... QuickBooks Time API access
continues to be managed separately."

QuickBooks Time actually runs its own older, separate API and OAuth2
system at rest.tsheets.com (docs: https://tsheetsteam.github.io/api_docs/),
nothing to do with developer.intuit.com at all. And for a single-company
integration like this dashboard - not a multi-tenant app serving many
different QuickBooks Time customers - TSheets' own docs recommend
skipping the OAuth authorization-code dance entirely:

    "If you need a small number of access tokens, implementing the full
    OAuth2 flow can be cumbersome... we allow access tokens to be created
    through the web UI via the API Add-on preferences page: Feature
    Add-ons -> API -> click 'Add a new application'... You can also
    extend the expiration date on these tokens via the web UI, so that
    you don't have to deal with refreshing tokens."

So this is intentionally just a thin wrapper around one static token from
settings - not a token-refreshing auth manager like zoho_auth.py. When
the token is getting close to its expiration, extend it from that same
QuickBooks Time page rather than generating a new one.
"""
from app.config.settings import settings


class TSheetsAuthError(Exception):
    """Raised when QuickBooks Time credentials are missing or invalid."""


class TSheetsAuth:
    def auth_header(self) -> dict:
        if not settings.TSHEETS_ACCESS_TOKEN:
            raise TSheetsAuthError(
                "Missing QuickBooks Time access token. Set TSHEETS_ACCESS_TOKEN "
                "in your .env file - generate one from inside QuickBooks Time "
                "itself (not developer.intuit.com): Feature Add-ons -> API -> "
                "Add a new application."
            )
        return {
            "Authorization": f"Bearer {settings.TSHEETS_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }


# Single shared instance, matching the zoho_auth/zoho_client pattern
tsheets_auth = TSheetsAuth()
