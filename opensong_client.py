import aiohttp
import asyncio
import logging
import os

from aiofile import async_open
from aiohttp.client_exceptions import ClientConnectorError, ClientOSError
from configparser import ConfigParser
from lxml import etree


log = logging.getLogger(__name__)
config = ConfigParser()
config.read(os.path.abspath(os.path.join(__file__, "..", "config.ini")))


class OpenSongAPI(object):
    def __init__(self, host: str, port: int):
        self.uri = f"http://{host}:{port}"
        self.session = aiohttp.ClientSession()

    async def get_slide_data(self, slide_id: int):
        uri = f"{self.uri}/presentation/slide/{slide_id}"
        async with self.session.get(uri) as resp:
            if resp.status == 200:
                return await resp.text()

            log.info(f"Received unexpected HTTP Status Code {resp.status}")
            log.info(await resp.text())
            return ""

    async def reset(self):
        await self.close()
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session:
            await self.session.close()


async def write_files(title: str, verses: list):
    async def write_file(path, data):
        async with async_open(path, "w") as fp:
            await fp.write(data)

    if not title:
        title = ""
    if not verses:
        verses = []

    files_to_write = []
    if title:
        title_str = title
    else:
        title_str = ""
    files_to_write.append(write_file(config["obs"]["title_file"], title_str))

    if not verses:
        verse_str = ""
    else:
        verse_str = ""
        for verse in verses:
            for line in verse.splitlines():
                verse_str += line + "\n"

        if verse_str.endswith("\n"):
            verse_str = verse_str[:-1]

    files_to_write.append(write_file(config["obs"]["verse_file"], verse_str))
    await asyncio.gather(*files_to_write)
    return


def sanitize_text(string):
    # Online reference: https://www.utf8-chartable.de/unicode-utf8-table.pl?start=8192
    sanitizations = {
        "\xe2\x80\x98": "'",   # LEFT SINGLE QUOTATION MARK
        "\xe2\x80\x99": "'",   # RIGHT SINGLE QUOTATION MARK
        "\xe2\x80\x9a": "'",   # SINGLE LOW-9 QUOTATION MARK
        "\xe2\x80\x9b": "'",   # SINGLE HIGH-REVERSED-9 QUOTATION MARK
        "\xe2\x80\x9c": "\"",  # LEFT DOUBLE QUOTATION MARK
        "\xe2\x80\x9d": "\"",  # LEFT RIGHT QUOTATION MARK
        "\xe2\x80\x9e": "\"",  # DOUBLE LOW-9 QUOTATION MARK
        "\xe2\x80\x9f": "\"",  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
        "\xe2\x80\xb9": "<",   # SINGLE LEFT-POINTING ANGLE QUOTATION MARK
        "\xe2\x80\xba": ">",   # SINGLE RIGHT-POINTING ANGLE QUOTATION MARK
    }
    for w_char in sanitizations.keys():
        if w_char in string:
            string = string.replace(w_char, sanitizations[w_char])

    return string


async def process_slide_xml(xml_data: str):
    title = ""
    verses = []
    xml = etree.fromstring(xml_data.encode())
    slide = xml.find("slide")
    if slide is not None:
        # Fetch Author/Title information from the slide
        song_title = slide.find("title")
        song_author = slide.find("author")
        song_ccli_number = slide.find("ccli")
        if song_title is not None and song_title.text is not None:
            # Prepend our title variable with the actual song title (in quotes)
            title += f"\"{song_title.text}\""
        if song_author is not None and song_author.text is not None:
            # Add the Author to the title variable
            title += f" - {song_author.text}"
        if song_ccli_number is not None and song_ccli_number.text is not None:
            # If there is a CCLI song number associated, add it to the title variable on a new line
            title += f"\nCCLI Song #{song_ccli_number.text}"

        # Iterate the "slides" element to find all 'body' elements which have the verse data
        verses = []
        for verses_slide in slide.findall("slides"):
            for k in verses_slide.iter():
                if k.tag == "body" and k.text is not None:
                    verses.append(sanitize_text(k.text))

    return title, verses


async def manage_websocket(host: str, port: int):
    uri = f"ws://{host}:{port}/ws"
    last_slide = 0
    async with aiohttp.ClientSession() as session:
        try:
            async with session.ws_connect(uri) as ws:
                log.info(f"Connected to '{uri}'")
                await ws.send_str("/ws/subscribe/presentation")
                log.info("Sent presentation subscription to WebSocket")
                api_session = OpenSongAPI(host, port)
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        msg_data = msg.data
                        if msg_data and msg_data.startswith("<?xml") and msg_data.endswith(">"):
                            xml = etree.fromstring(msg_data.encode())
                            # The WebSocket provides a small amount of data about the current status of OpenSong
                            # Just parse all of the elements and extract out what is interesting
                            presentation_mode = False
                            slide_number = 0
                            for child in xml.iter():
                                if child.tag == "presentation":
                                    presentation_mode = bool(int(child.attrib.get("running", "0")))
                                elif child.tag == "slide":
                                    slide_number = int(child.attrib.get("itemnumber", "0"))

                            if presentation_mode:
                                if last_slide != slide_number:
                                    log.info(f"WebSocket received update, transitioned from slide '{last_slide}' to "
                                             f"slide'{slide_number}'")
                                    if slide_number > 0:
                                        slide_data = await api_session.get_slide_data(slide_number)
                                        title, verses = await process_slide_xml(slide_data)
                                        await write_files(title, verses)

                                    last_slide = slide_number
                                else:
                                    log.info(f"WebSocket received update, slide remains at: '{slide_number}'")
                            else:
                                log.info("Presentation is not running")
                                # Reset the session here, so that we can attempt to better handle a race condition where
                                # we could be about to send a request to the REST server when someone may decide to
                                # close the presentation mode.
                                await api_session.reset()

                        else:
                            if msg_data == "The requested action is not available.":
                                log.info("Client is already subscribed to the WebSocket, waiting for new messages...")
                            elif msg_data == "OK":
                                log.info("Client is connected and OpenSong is running")
                            else:
                                log.info(f"Received unknown non-XML response: {msg_data}")

            # If we're here, the connection to the websocket was closed (most likely OpenSong was closed)
            logging.info("Disconnected from websocket")
            # Clean up the REST API session
            await api_session.close()

        except (ClientConnectorError, ClientOSError):
            return


def check_config():
    ret = True
    checks = {
        "opensong": ["host", "port"],
        "obs": ["title_file", "verse_file"]
    }

    config_keys = config.sections()
    for config_item in checks:
        if config_item not in config_keys:
            log.info(f"Config is missing key '{config_item}'")
            ret = False
            continue

        for sub_item in checks[config_item]:
            check = config[config_item].get(sub_item, "")
            if not check:
                log.info(f"Config key '{config_item}' is missing or has an empty value for sub-item '{sub_item}'")
                ret = False

    return ret


async def main():
    if not check_config():
        log.info("Exiting due to invalid config file values")
        return

    await write_files("", [])
    while True:
        await manage_websocket(config["opensong"]["host"], int(config["opensong"]["port"]))
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s: %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S")
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        log.info("Exiting ...")
