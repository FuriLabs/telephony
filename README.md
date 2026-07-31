# Telephony - combined dialer, sms/mms and contactbook - first stable release 1.00

**Telephony** is a lightning-fast GTK4 + Libadwaita communication suite designed for **FuriLabs phones (FLX1 / FLX1s)** running Phosh. It serves as a complete replacement for the default dialer, contacts app and messaging apps.

**Translated** we support now over 100 languages but help is needed since most of them are AI generated translations so I have no idea what they really say :)

### Why?
Because I did not understand how to fix some issues what annoyed me I started to build project which I could understand - this is not best method but hey it is something, right? 
Telephony interacts directly with `ofono` and the Evolution Data Server to provide a seamless experience.

**Note:** With this release, Telephony can replace **GNOME Calls** and **Chatty**. You no longer need them installed to have a fully functional phone.

---

### Upgrading from experimental builds

If you have been using the experimental Telephony builds, run this once to migrate your contact cache to the new stable database model:

```bash
rm -f ~/.local/share/telephony/contacts.db ~/.local/share/telephony/contacts.db-wal ~/.local/share/telephony/contacts.db-shm
```

This only removes the local contact *cache* - Telephony rebuilds it automatically from your address books on the next start. Your contacts, call history and messages are not touched. Fresh installs do not need this.

---

### What can Telephony do?

#### Advanced Calling
* **Rapid Fast:** The dialer starts instantly and scrolls through massive contact lists without lag because everything is loaded efficiently into RAM.
* **Pro Call Management:** Handle multiple active calls with **Call Waiting (Hold & Swap)** support.
* **In-Call Controls:** A full in-call menu featuring Mute, Speaker, Hold, and keypad access.
* **Proximity Fader:** Built-in proximity sensor handling to fade the screen and prevent accidental cheek presses during calls.
* **Quick Decline:** Busy? Reject an incoming call with a single tap that automatically sends a customizable *"I can't talk right now"* SMS response.
* **Anonymous Calling:** A dedicated toggle to instantly "Hide Caller ID" for your next call.
* **Smart Keypad:** Fully functional input field with cursor support. Edit numbers, delete specific digits, or paste into the middle of a number.
* **Advanced Call History:** View call duration and detailed timestamps. Filter and sort history by duration, date, or call type. Now featuring **History Search** to find calls instantly.
* **We have now also lockscreen actions** Answer, hangup, switch speaker/earpiece, mute mic, swap calls trough lockscreen
* **We support also Phosh Emergency Calls setup** You can add your own "emergency numbers" trough Telephony Settings and that will allow to call to those numbers from lockscreen directly.
* **Your carrier does not give you ringbacktone?** No problem with Telephony you can use what ringbacktone you wan't and all is easily configured behind Settings page

#### Rich Messaging
* **Full MMS Support:** Send and receive **SMS and Group MMS**. The app automatically handles group threading and correctly identifies participants. This is now 100% accurate since the 0.9 release
* **Smart Attachments:** Send images and files with automatic compression to fit carrier limits. View incoming media instantly with **low-RAM thumbnails** and improved handling for all video types.
* **File Handling:** Mobile-optimized "Open With" and "Save As" dialogs for all attachments.
* **Modem Hygiene:** Automatically detects and cleans up "ghost" messages from the modem to keep your database clean.
* **Smart Notifications:** Tightly integrated with Phosh. Notifications are persistent only if missed. If you open a chat, the specific notification is automatically cleared.
* **Take pictures, record video or voice message directly** You can use Telephonys built-in camera or voice-recorder for sending live moments as MMS message
* **Search for all Messages** You can search trough all messages and click results - Boom it will open that chat and focus on clicked message (feature inspired from Telegram)

#### Contacts & System
* **Trusted SMS Actions:** Telephony includes a suite of secret SMS actions that can be triggered by trusted contacts:
  * **Find my FLX1/FLX1s:** Add a trusted contact and a secret message. When that contact sends you the secret message, we automatically reply with your location (OpenStreetMap link).
  * **Trusted Callback:** Send a secret message to trigger the phone to automatically call you back.
  * **SMS Relay:** Relay messages to other numbers through your device remotely.
  * **SMS tmate:** Remotely initiate a secure `tmate` reverse shell session via SMS.
  * **Wipe Device:** Remotely wipe the device in emergency situations using a secret message and credentials.
* **Easy Contact Management:** Add unknown callers to your address book easily. We interact directly with the Evolution Data Server (EDS) so contacts sync immediately.
* **Data Portability:** Import or Export contacts via **VCard (.vcf)** files with a single click.
* **Smart Paste:** Copy a messy text like *"Call him! +1 (234) 567-8900"* and hit Paste. Telephony intelligently strips the text and keeps only the number.
* **Adaptive UI:** A polished interface that handles long contact names gracefully without breaking the layout. All this libadwaita native mobile styling.
* **Multi-Addressbook Support:** Combine contacts from multiple sources (e.g., Local, Nextcloud, Google) into a single view. Configure the priority of your address books in Settings. The topmost address book is used for resolving caller IDs and is the default for saving new contacts.
* **Optional Duplicate Resolver Banner:** Automatically scan for identical numbers across address books. The duplicate resolver banner can now be toggled on or off directly from Settings.

### CLI Usage
Telephony includes a full-featured CLI to interact with its daemon. You can use it to dial, send SMS/MMS, manage contacts, adjust settings, and more.
**Note:** The CLI communicates over D-Bus with the main Telephony daemon (`app.py`/`main.py`). The daemon must be actively running in a valid session environment (e.g., Phosh running on FuriOS). If you are running the CLI in a headless environment or testing outside of a display session, you will need to wrap the commands in `dbus-run-session` or ensure a standard dbus daemon is available.

---

### Known Limitations
* **Build with duct tape, hot glue and zipper ties...** if this makes you uncomfortable this might not be your project 😂. I am not sure what is missing or what does not work so you tell me? For me it is rock solid

---

### How to Install

Telephony is now packaged properly for Debian. You can build it from source easily or just download the deb and install that.

#### Build Dependencies
First, ensure you have the necessary build tools and test dependencies (like `pyflakes` for static analysis):
```bash 
sudo apt install git build-essential debhelper dh-python python3-all python3-setuptools pybuild-plugin-pyproject python3-pytest python3-pyflakes
```

#### Build and Install
1. Clone the repository:

```bash 
git clone https://github.com/FuriLabs/telephony.git
```
```bash 
cd telephony
```

2. Build the package:

```bash 
dpkg-buildpackage -us -uc
```

3. Install the resulting package:

```bash 
cd ..
```

```bash 
sudo apt install ./furios-app-telephony_*.deb
```

4. Disable ofono-toned, Chatty and Calls
```bash 
# Stop the service if running
systemctl --user stop calls-daemon && \
# Mask the systemd service
systemctl --user mask calls-daemon && \
# Divert (disable) the autostart daemons
sudo dpkg-divert --add --rename --divert /etc/xdg/autostart/sm.puri.Chatty-daemon.desktop.disabled /etc/xdg/autostart/sm.puri.Chatty-daemon.desktop && \
sudo dpkg-divert --add --rename --divert /etc/xdg/autostart/org.gnome.Calls-daemon.desktop.disabled /etc/xdg/autostart/org.gnome.Calls-daemon.desktop && \
# Divert (hide) the desktop launchers/icons
sudo dpkg-divert --add --rename --divert /usr/share/applications/sm.puri.Chatty.desktop.disabled /usr/share/applications/sm.puri.Chatty.desktop && \
sudo dpkg-divert --add --rename --divert /usr/share/applications/org.gnome.Calls.desktop.disabled /usr/share/applications/org.gnome.Calls.desktop && \
# Create the dummy DBus service to prevent activation
mkdir -p ~/.local/share/dbus-1/services/ && \
echo -e "[D-BUS Service]\nName=org.gnome.Calls\nExec=/bin/true" > ~/.local/share/dbus-1/services/org.gnome.Calls.service && \
echo "Calls and Chatty have been disabled and hidden." && \
# Disable ofono-toned
systemctl --user disable ofono-toned && \
systemctl --user mask ofono-toned && \
pkill -f ofono-toned && \
echo "Ofono-toned is now masked and killed."
```
---

### How to Uninstall

Because it is a proper system package now, you can remove it cleanly using standard tools:

```bash 
sudo apt remove furios-app-telephony
```

Restore ofono-toned, Calls and Chatty
```bash
# Unmask the service
systemctl --user unmask calls-daemon && \
# Remove the diversions (restore autostart and icons)
sudo dpkg-divert --remove --rename /etc/xdg/autostart/sm.puri.Chatty-daemon.desktop && \
sudo dpkg-divert --remove --rename /etc/xdg/autostart/org.gnome.Calls-daemon.desktop && \
sudo dpkg-divert --remove --rename /usr/share/applications/sm.puri.Chatty.desktop && \
sudo dpkg-divert --remove --rename /usr/share/applications/org.gnome.Calls.desktop && \
# Remove the dummy DBus service
rm -f ~/.local/share/dbus-1/services/org.gnome.Calls.service && \
# Reload the daemon (optional but good practice)
systemctl --user daemon-reload && \
echo "Calls and Chatty have been restored. You may need to reboot or relogin."  && \
# Disable ofono-toned
systemctl --user unmask ofono-toned && \
systemctl --user enable ofono-toned && \
systemctl --user start ofono-toned && \
echo "Ofono-toned is now unmasked, enabled and started."
```

---

### License

**GPL-3.0-or-later**

Feel free to do whatever you want with this but **no guarantees** this will probably explode your phone xD.

### Feedback
If you want to help or find an issue, feel free to contact or open an issue on this repo.
