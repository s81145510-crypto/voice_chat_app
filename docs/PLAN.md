# Twenty One Party: የማዋሃድ እቅድ (2026-11-27)

ምንጭ፦ የባለቤቱ "Complete System Development Master Prompt"። የባለቤቱ ቅደም ተከተል፦ **መጀመሪያ app-ው ይጠናቀቃል፤ security፣ database እና payment በመጨረሻ።**
ይህ ንጽጽር ኮዱን በማየት የተሠራ ነው፤ ሙሉ ፍተሻ ወይም የ security ግምገማ አይደለም።

## 1. አሁን ያለውና የሚቀረው
| ክፍል | አለ | ይቀራል |
|---|---|---|
| መለያ | ስልክ+password፣ Telegram ማረጋገጫ፣ logout፣ admin reset፣ ban | ራስ account መሰረዝ |
| Home | live/party ዝርዝር፣ follow፣ ranking | notifications |
| Live | ቪዲዮ፣ chat፣ ስጦታ፣ follow፣ guest mute/kick | ሙሉ የተመልካች ዝርዝር፣ like፣ chat block |
| Voice party | 21 seats፣ mute/kick፣ invite | room admins፣ private room |
| PK | 5 ደቂቃ፣ ነጥብ፣ አሸናፊ፣ draw | PK history ገጽ |
| ሽልማት | daily check-in streak፣ missions | Host activity reward፣ የ7 ቀን welcome |
| ሚናዎች | User፣ Host፣ Agent፣ Admin | Moderator፣ Super Admin |
| ገንዘብ | coin ግዢ እና withdraw በ admin፣ seller፣ finance API | ክፍል 2ን ተመልከት |

## 2. ከሰነዱ ጋር የማይጣጣሙ
1. **ኮሚሽን፦** live ላይ የሚሠራው ነባሪ ባለ 3 ደረጃ ነው (ከቀሪው ድርሻ 30/15/5%)። በኮዱ ውስጥ `seven12` የሚባል አማራጭ አለ፤ ግን ከ **ቀሪው 50%** ላይ ያሰላል። ሁሉም ደረጃ ቢኖር ከስጦታው **6%** ይሆናል እንጂ ሰነዱ እንደሚለው 12% አይሆንም።
2. **ትንሽ ስጦታ፦** በሙሉ coin ወደ ታች ስናጠጋጋ የ 10 coin ስጦታ ኮሚሽን በሁሉም ደረጃ **0** ነው። ከማገናኘታችን በፊት ኮሚሽኑ በትንሽ ክፍልፋይ (ሳንቲም ወይም milli-coin) መቀመጥ አለበት።
3. **Agent ድርሻ፦** 50 + 12 + 38 = 100 ስለሆነ በተራ ስጦታ ውስጥ የተለየ Agent ድርሻ የለም፤ ሰነዱ ግን Agent Commission-ን እንደ ገቢ ይጠቅሳል።
4. **የ coin ዋጋ፦** ሰነዱ ለሒሳብ ማጣቀሻ 1 coin = 0.10 ETB ይላል። app-ው withdraw ሲያሰላ 0.40 ETB ይጠቀማል፤ በ package 0.42–0.50 ETB ይሸጣል። በ payment ደረጃ መወሰን አለበት።
5. **Betting/Lucky፦** ሰነዱ ሕጋዊ ግምገማ እስኪደረግ **ዝግ** ይሁን ይላል፤ አሁን **ክፍት** ናቸው።
6. **PK 70/20/10፦** ሰነዱ ግልጽ ፈቃድ ይፈልጋል ይላል፤ አሁን live ላይ **ይሠራል።**
7. **ሒሳብ አያያዝ፦** ኮዱ float ይጠቀማል፤ ledger-ው double-entry አይደለም።
8. **Withdraw፦** አሁን ዝቅተኛው 500 coin ነው፤ ሰነዱ 1,000 ETB እና 50% genuine gift earnings ይጠይቃል።

## 3. የተጻፈ ኮድ (2026-11-27 ምሽት ተሻሽሏል)
በ `phase4` ላይ ተሠርቷል፤ ወደ `main` እስኪገባ live አይሆንም።
- **7 generation ኮሚሽን** ባለ 3 ደረጃውን ተክቷል። ሰንሰለቱ ከስጦታ ተቀባዩ በላይ ያሉ ጋባዦች ናቸው፤ መሠረቱ በስጦታው ውስጥ ያለው የተገዛ coin ነው። ክፍልፋዮች ተጠራቅመው ይከፈላሉ። (ክፍል 2 ቁጥር 1–3 በዚህ ተፈትቷል።)
- Host ላልሆነ ሰው የሚላክ luxury ስጦታ ከ70% ወደ 50% ወርዷል።
- የተገዛ coin ስሌት ላይ የነበረው የአንድ ጊዜ ጥያቄዎች ክፍተት ተዘግቷል።
- የግብዣ link ወደ web app ይወስዳል።
- Activity reward እና income exchange ስሌቶች `rules.py` ውስጥ አሉ፤ **ገና አልተገናኙም።**
- ሙከራ፦ `python -m unittest discover -s tests`። የሒሳብ ሙከራ ብቻ ነው፤ database እና API **አልተሞከሩም።**

## ደረጃ A፣ ዙር 1 (2026-11-28፣ `phase4`)
- በ app ውስጥ notifications (follow፣ live፣ ስጦታ፣ የስርዓት መልዕክት)፣ የተመልካች ዝርዝር፣ live like ❤️፣ PK history ተጽፈዋል (`backend/social.py`፤ አዲስ table፦ `social_notice`፣ `social_room_like`)።
- ስክሪኖቹ በአስመሳይ server ተሞክረዋል። Backend ከ database ጋር **አልተሞከረም።**
- ዙር 2 የቀረ፦ chat block፣ room admins፣ private room፣ account መሰረዝ፣ Moderator።

## ደረጃ A፣ ዙር 2 (2026-11-28፣ `phase4`)
- room admin፣ chat mute፣ remove/block፣ private room (password)፣ report፣ Moderator፣ ተጠቃሚው account-ውን መዝጋት ተጽፈዋል (`backend/moderation.py`፣ `backend/roomrules.py`፤ አዲስ table ብቻ `mod_*`)።
- Super Admin = በ `ADMIN_IDS` ያሉት። Moderator ወደ coin፣ ክፍያ እና ስልክ ቁጥር መድረሻ የለውም።
- የተዘጋ account አይሰረዝም፤ ይደበቃል፣ balance-ው ታግዶ ይቆያል፣ የክፍያ መዝገብ ይቀመጣል።
- ፈቃድ ደንቦች በ unit test ተፈትሸዋል፤ ስክሪኖች በአስመሳይ server። Backend ከ database ጋር **አልተሞከረም።**
- የሚታወቅ ገደብ (ደረጃ D)፦ block የተደረገ ሰው የተቀየረ app ከተጠቀመ ቀድሞ የወሰደው Agora token እስከ 1 ሰዓት ሊሠራ ይችላል (Agora kicking-rule API ያስፈልጋል)።

## 4. ቅደም ተከተል
- **A. App ማሟላት፦** notifications፣ ተመልካች ዝርዝር፣ like፣ room moderation እና privacy፣ PK history፣ account መሰረዝ፣ Moderator
- **B. Host activity፦** server የሚቆጥረው ጊዜ፣ ሽልማት፣ የ7 ቀን welcome፣ የተግባር ገጽ። ሐሰተኛ እንቅስቃሴን ሙሉ በሙሉ ለመከላከል በ D ተጨማሪ ማረጋገጫ ያስፈልጋል።
- **C. 7 ደረጃ ኮሚሽን እና Income exchange፦** exchange እስከ D መጨረሻ **OFF**
- **D. በመጨረሻ፦** security፣ database migration እና backup፣ double-entry ledger፣ Telebirr፣ withdraw፣ fraud፣ TEST 7–10፣ deployment

**D እስኪጠናቀቅ እውነተኛ ገንዘብ አይመከርም።** አሁን ያለው app ግን coin ግዢ እና withdraw ጥያቄዎችን አስቀድሞ ይቀበላል።
ነፃው Render database December 24, 2026 ያበቃል።

## 5. ውሳኔዎች
- ባለቤቱ፦ ባለ 3 ደረጃው ወደ 7 generation ይቀየር፤ app-ው ትርፋማ፣ ሳቢ እና ኪሳራ የሌለው ይሁን።
- የተመረጠ (ባለቤቱ ሊቀይረው ይችላል)፦ ሰንሰለቱ የስጦታ ተቀባዩ ጋባዦች ናቸው።
- አልተቀየሩም፦ Betting games፣ Lucky gift፣ PK 70/20/10።
- ክፍት፦ የ7 ቀን welcome መነሻ ቀን፤ የ Host Level አሰላል።

## 6. የኪሳራ አደጋዎች
- **USDT withdraw፦** `USDT_BIRR` ከገበያ ዋጋ በጣም ዝቅ ካለ የሚከፈለው USDT በ Birr ውድ ይሆናል። በ PK እና በዝቅተኛው ሽያጭ ዋጋ ከ 178 በላይ የገበያ ዋጋ ኪሳራ ያመጣል (በ `USDT_BIRR=161` ከሆነ)።
- **ሐሰተኛ የክፍያ screenshot፦** ከማጽደቅ በፊት ገንዘቡ መግባቱን በራስህ Telebirr/USDT account ማረጋገጥ።
- hosting፣ Agora፣ የክፍያ ክፍያዎች፣ ግብር እና refund በስሌቱ ውስጥ የሉም።
