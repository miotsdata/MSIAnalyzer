# Resource object code (Python 3)
# Created by: object code
# Created by: The Resource Compiler for Qt version 6.11.1
# WARNING! All changes made in this file will be lost!

from PySide6 import QtCore

qt_resource_data = b"\
\x00\x00\x04w\
/\
/ src/msianalyze\
r/gui/qml/Main/M\
ain.qml\x0aimport Q\
tQuick\x0aimport Qt\
Quick.Controls\x0ai\
mport QtQuick.La\
youts\x0aimport QtQ\
uick.Dialogs\x0a\x0aAp\
plicationWindow \
{\x0a    id: window\
\x0a    visible: tr\
ue\x0a    width: 80\
0\x0a    height: 60\
0\x0a    minimumWid\
th: 500\x0a    mini\
mumHeight: 400\x0a \
   title: \x22MSIAn\
alyzer\x22\x0a\x0a    Col\
umnLayout {\x0a    \
    anchors.cent\
erIn: parent\x0a   \
     spacing: 16\
\x0a\x0a        Button\
 {\x0a            i\
d: loadProjectBu\
tton\x0a           \
 objectName: \x22lo\
adProjectButton\x22\
\x0a            tex\
t: \x22Load Project\
\x22\x0a            La\
yout.preferredWi\
dth: 220\x0a       \
     Layout.alig\
nment: Qt.AlignH\
Center\x0a         \
   onClicked: pr\
ojectFolderDialo\
g.open()\x0a       \
 }\x0a\x0a        Butt\
on {\x0a           \
 id: createProje\
ctButton\x0a       \
     objectName:\
 \x22createProjectB\
utton\x22\x0a         \
   text: \x22Create\
 Project\x22\x0a      \
      Layout.pre\
ferredWidth: 220\
\x0a            Lay\
out.alignment: Q\
t.AlignHCenter\x0a \
           //onC\
licked: appBridg\
e.createProject(\
)\x0a        }\x0a    \
}\x0a\x0a    FolderDia\
log {\x0a        id\
: projectFolderD\
ialog\x0a        ob\
jectName: \x22proje\
ctFolderDialog\x22\x0a\
        options:\
 FolderDialog.Do\
ntUseNativeDialo\
g\x0a        //onAc\
cepted: appBridg\
e.setProjectFold\
er(selectedFolde\
r.toString())\x0a  \
  }\x0a}\x0a\
\x00\x00\x00\x1e\
m\
odule Main\x0aMain \
1.0 Main.qml\x0a\
"

qt_resource_name = b"\
\x00\x04\
\x00\x057\xfe\
\x00M\
\x00a\x00i\x00n\
\x00\x08\
\x08\x01^\x5c\
\x00M\
\x00a\x00i\x00n\x00.\x00q\x00m\x00l\
\x00\x06\
\x07\x84+\x02\
\x00q\
\x00m\x00l\x00d\x00i\x00r\
"

qt_resource_struct = b"\
\x00\x00\x00\x00\x00\x02\x00\x00\x00\x01\x00\x00\x00\x01\
\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x00\x00\x02\x00\x00\x00\x02\x00\x00\x00\x02\
\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00$\x00\x00\x00\x00\x00\x01\x00\x00\x04{\
\x00\x00\x01\xa0C\x10\x19\xe2\
\x00\x00\x00\x0e\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\
\x00\x00\x01\xa0CUgG\
"

def qInitResources():
    QtCore.qRegisterResourceData(0x03, qt_resource_struct, qt_resource_name, qt_resource_data)

def qCleanupResources():
    QtCore.qUnregisterResourceData(0x03, qt_resource_struct, qt_resource_name, qt_resource_data)

qInitResources()
