"""AlexaLogin that signs in on the account's own Amazon site.

AlexaPy always starts the sign-in at www.amazon.com. Amazon India (and other non-.com) accounts are separate
accounts even with the same email, so that signs in to the wrong account (The user got one with only two test
switches). alexa-cookie2 (ioBroker / Node-RED) signs in at www.<region> with the app handle suffixed by the
region, e.g. amazon.in -> amzn_dp_project_dee_ios_in. The steps after sign-in already use api.<region>.
"""
from alexapy import AlexaLogin


class RegionLogin(AlexaLogin):
    @property
    def start_url(self):
        url = super().start_url
        if not self.oauth_login or self.url == "amazon.com":
            return url
        handle = "amzn_dp_project_dee_ios_" + self.url.rsplit(".", 1)[-1]  # amazon.in -> ..._in
        query = dict(url.query, **{"openid.return_to": f"https://www.{self.url}/ap/maplanding",
                                   "openid.assoc_handle": handle, "pageId": handle})
        return url.with_host(f"www.{self.url}").with_query(query)
