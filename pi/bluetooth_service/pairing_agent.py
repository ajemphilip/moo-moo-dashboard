"""BlueZ pairing agent for headless, encrypted Moo BLE connections."""

import logging

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType
from dbus_next.service import ServiceInterface, method

logger = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
BLUEZ_PATH = "/org/bluez"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_PATH = "/com/moo/setup/pairing_agent"
AGENT_CAPABILITY = "NoInputNoOutput"


class MooPairingAgent(ServiceInterface):
    """Accept Bluetooth LE Just Works pairing for a headless Moo device."""

    def __init__(self) -> None:
        super().__init__(AGENT_INTERFACE)

    @method()
    def Release(self):
        logger.info("BlueZ released the Moo pairing agent")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        logger.info("Accepting legacy PIN pairing request from %s", device)
        return "000000"

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        logger.info("BlueZ displayed a PIN for %s", device)

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        logger.info("Accepting legacy passkey request from %s", device)
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        logger.info("BlueZ displayed a passkey for %s", device)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        logger.info("Accepting Just Works confirmation from %s", device)

    @method()
    def RequestAuthorization(self, device: "o"):
        logger.info("Authorizing Bluetooth pairing from %s", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        logger.info("Authorizing Bluetooth service %s for %s", uuid, device)

    @method()
    def Cancel(self):
        logger.info("BlueZ cancelled a pairing request")


class BlueZPairingAgent:
    """Own the D-Bus registration for the lifetime of the BLE bridge."""

    def __init__(self) -> None:
        self.bus = None
        self.manager = None
        self.agent = MooPairingAgent()
        self.registered = False

    async def start(self) -> None:
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.bus.export(AGENT_PATH, self.agent)
        introspection = await self.bus.introspect(BLUEZ_SERVICE, BLUEZ_PATH)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, BLUEZ_PATH, introspection)
        self.manager = proxy.get_interface(AGENT_MANAGER_INTERFACE)
        await self.manager.call_register_agent(AGENT_PATH, AGENT_CAPABILITY)
        self.registered = True
        await self.manager.call_request_default_agent(AGENT_PATH)
        logger.info("Registered the headless Moo Bluetooth pairing agent")

    async def stop(self) -> None:
        if self.registered and self.manager is not None:
            try:
                await self.manager.call_unregister_agent(AGENT_PATH)
            except Exception:
                logger.exception("Could not unregister the Moo pairing agent")
        self.registered = False
        if self.bus is not None:
            self.bus.disconnect()
        self.bus = None
        self.manager = None
