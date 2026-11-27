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

## 3. የተጻፈ ኮድ
`backend/rules.py`፦ የስጦታ ክፍፍል፣ 7 ደረጃ ኮሚሽን፣ activity reward እና income → coin ስሌቶች። **ከ live app ጋር ገና አልተገናኘም።**
ሙከራ፦ `python -m unittest discover -s tests`። እነዚህ የሰነዱን TEST 1–6 **የሒሳብ ክፍል ብቻ** ይፈትናሉ። database፣ journal፣ የ wallet ገደቦች፣ የተረጋገጠ የ host ጊዜ እና TEST 7–10 **አልተሞከሩም።**

## 4. ቅደም ተከተል
- **A. App ማሟላት፦** notifications፣ ተመልካች ዝርዝር፣ like፣ room moderation እና privacy፣ PK history፣ account መሰረዝ፣ Moderator
- **B. Host activity፦** server የሚቆጥረው ጊዜ፣ ሽልማት፣ የ7 ቀን welcome፣ የተግባር ገጽ። ሐሰተኛ እንቅስቃሴን ሙሉ በሙሉ ለመከላከል በ D ተጨማሪ ማረጋገጫ ያስፈልጋል።
- **C. 7 ደረጃ ኮሚሽን እና Income exchange፦** exchange እስከ D መጨረሻ **OFF**
- **D. በመጨረሻ፦** security፣ database migration እና backup፣ double-entry ledger፣ Telebirr፣ withdraw፣ fraud፣ TEST 7–10፣ deployment

**D እስኪጠናቀቅ እውነተኛ ገንዘብ አይመከርም።** አሁን ያለው app ግን coin ግዢ እና withdraw ጥያቄዎችን አስቀድሞ ይቀበላል።
ነፃው Render database December 24, 2026 ያበቃል።

## 5. የሚጠበቁ ውሳኔዎች
1. የ7 ደረጃ ሰንሰለቱ የማን ነው? (ሐሳብ፦ ስጦታ ተቀባዩን Host የጋበዙት፤ ባለ 3 ደረጃው ይቆማል)
2. Betting games እና Lucky gift ይዘጉ? (ሐሳብ፦ አዎ፣ ኮዱ ሳይሰረዝ በ switch)
3. PK 70/20/10 እና draw 45/45/10 ጸድቋል?
4. Welcome ቀን 1 ከመቼ ይጀምራል? Host Level እንዴት ይሰላል? (ሐሳብ፦ Host ከጸደቀበት ቀን ጀምሮ 7 ቀን፤ Host Level ከተቀበለው የተገዛ-coin ስጦታ፣ ደረጃዎቹ በኋላ ይጸድቃሉ)
