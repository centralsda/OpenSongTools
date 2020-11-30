# OpenSongTools
Currently this repository simply houses a command line based python script which is connects to an OpenSong WebSocket
(thus requires the REST API via Settings > General Settings > System > Automation API). The script will subscribe to
a presentation endpoint using the websocket so that it can receive notifications of when an OpenSong presentation
navigates the slide show. Upon receiving said update, the script will request the XML of the slide so that it can pull
specific pieces of metadata from the slide. This is then translated into text files to be read by OBS and displayed
in a scene. More details are described below.


## opensong_client.py
A (mostly) asynchronous script which has three primary functions:
1. Connect to an OpenSong WebSocket
2. Pull slide information when OpenSong is in presentation mode (inferred via data from WebSocket)
3. Write metadata to a pair of files which OBS can read

The logic for this script will try to create a connection to the WebSocket. If it fails, it will simply retry after a
small amount of time. Once a connection has been established to the WebSocket it will subscribe to the
`/ws/subscribe/presentation` endpoint.

From here, the script will simply listen for notifications. If a notification is received that the OpenSong presentation
has moved to a different slide, a HTTP request to the REST endpoint `/presentation/slide/{slide_id}` is made to pull in
the XML for that slide. It then parses the XML to retrieve the following information:
- Song Title
- Song Author(s)
- CCLI Number
- Verse(s)

Once the above information is gathered, it is written to a pair of files so that (in our case) two text sources can be
populated for a scene in OBS. The Song Author/Title/CCLI information is presented in a "top bound" source, where the
verses are presented in a "bottom bound" source. Thus, text files for `title_file` and `verse_file` are located in the
configuration file for these reasons respectively.

## Requirements
- `Python >= 3.8` (tested and developed with 3.9.0, likely will work with 3.7.x)
- `lxml` (Much more performant than builtin xml.etree)

## Acknowledgements
- [Vwout](https://sourceforge.net/p/opensong/support-requests/259/)