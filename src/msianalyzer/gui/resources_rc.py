# Resource object code (Python 3)
# Created by: object code
# Created by: The Resource Compiler for Qt version 6.11.1
# WARNING! All changes made in this file will be lost!

from PySide6 import QtCore

qt_resource_data = b"\
\x00\x00\x03\xf8\
i\
mport QtQuick\x0aim\
port QtQuick.Con\
trols\x0aimport QtQ\
uick.Layouts\x0aimp\
ort QtQuick.Dial\
ogs\x0a//import Cor\
e\x0a\x0aPage {\x0a    id\
: startPage\x0a\x0a   \
 ColumnLayout {\x0a\
        anchors.\
centerIn: parent\
\x0a        spacing\
: 16\x0a\x0a        Bu\
tton {\x0a         \
   id: loadProje\
ctButton\x0a       \
     objectName:\
 \x22loadProjectBut\
ton\x22\x0a           \
 text: \x22Load Pro\
ject\x22\x0a          \
  Layout.preferr\
edWidth: 220\x0a   \
         Layout.\
alignment: Qt.Al\
ignHCenter\x0a\x0a    \
        onClicke\
d: projectFolder\
Dialog.open()\x0a  \
      }\x0a\x0a       \
 Button {\x0a      \
      id: create\
ProjectButton\x0a  \
          object\
Name: \x22createPro\
jectButton\x22\x0a    \
        text: \x22C\
reate Project\x22\x0a \
           Layou\
t.preferredWidth\
: 220\x0a          \
  Layout.alignme\
nt: Qt.AlignHCen\
ter\x0a\x0a           \
 onClicked: Rout\
er.createProject\
Requested()\x0a    \
    }\x0a    }\x0a\x0a   \
 FolderDialog {\x0a\
        id: proj\
ectFolderDialog\x0a\
        objectNa\
me: \x22projectFold\
erDialog\x22\x0a\x0a     \
   options: Fold\
erDialog.DontUse\
NativeDialog\x0a   \
     onAccepted:\
 {\x0a            R\
outer.projectFol\
derChosen(select\
edFolder.toStrin\
g())\x0a        }\x0a \
   }\x0a}\x0a\
\x00\x00\x00)\
m\
odule Views\x0aStar\
tPage 1.0 StartP\
age.qml\x0a\
\x00\x00\x01\x13\
i\
mport QtQuick\x0aim\
port QtQuick.Con\
trols\x0a\x0aApplicati\
onWindow {\x0a    i\
d: window\x0a\x0a    v\
isible: true\x0a   \
 width: 900\x0a    \
height: 650\x0a    \
title: \x22MSIAnaly\
zer\x22\x0a\x0a    StackV\
iew {\x0a        id\
: stackView\x0a    \
    anchors.fill\
: parent\x0a\x0a      \
  initialItem: \x22\
qrc:/Views/Start\
Page.qml\x22\x0a    }\x0a\
}\x0a\
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
\x00\x05\
\x00\x5c\xfc\xe3\
\x00V\
\x00i\x00e\x00w\x00s\
\x00\x0d\
\x02\x89g\x9c\
\x00S\
\x00t\x00a\x00r\x00t\x00P\x00a\x00g\x00e\x00.\x00q\x00m\x00l\
\x00\x06\
\x07\x84+\x02\
\x00q\
\x00m\x00l\x00d\x00i\x00r\
\x00\x08\
\x08\x01^\x5c\
\x00M\
\x00a\x00i\x00n\x00.\x00q\x00m\x00l\
"

qt_resource_struct = b"\
\x00\x00\x00\x00\x00\x02\x00\x00\x00\x02\x00\x00\x00\x01\
\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x00\x00\x02\x00\x00\x00\x02\x00\x00\x00\x05\
\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x0e\x00\x02\x00\x00\x00\x02\x00\x00\x00\x03\
\x00\x00\x00\x00\x00\x00\x00\x00\
\x00\x00\x00\x1e\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\
\x00\x00\x01\xa0C\xf2n\xb8\
\x00\x00\x00>\x00\x00\x00\x00\x00\x01\x00\x00\x03\xfc\
\x00\x00\x01\xa0C\x92\x9f\x9a\
\x00\x00\x00>\x00\x00\x00\x00\x00\x01\x00\x00\x05@\
\x00\x00\x01\xa0C\x10\x19\xe2\
\x00\x00\x00P\x00\x00\x00\x00\x00\x01\x00\x00\x04)\
\x00\x00\x01\xa0Gr\x89\xa6\
"

def qInitResources():
    QtCore.qRegisterResourceData(0x03, qt_resource_struct, qt_resource_name, qt_resource_data)

def qCleanupResources():
    QtCore.qUnregisterResourceData(0x03, qt_resource_struct, qt_resource_name, qt_resource_data)

qInitResources()
