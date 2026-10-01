from typing import Literal

from pydantic import BaseModel

from api.services.telephony.providers.sip_trunk.sip_config import SIPTrunkConfiguration


class SIPTrunkConfigurationRequest(BaseModel):
    provider: Literal["sip_trunk"] = "sip_trunk"
    sip: SIPTrunkConfiguration