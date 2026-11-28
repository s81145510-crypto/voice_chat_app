# Twenty One Party — Production Deployment Checklist

ይህ ሰነድ የDeploy ቅድመ-ሁኔታዎችን ይዘረዝራል። ሰነዱ ብቻውን የDeploy ፈቃድ አይደለም።

## 1. Hosting እና Database

- [ ] ለproduction PostgreSQL 16 ወይም ተስማሚ የተደገፈ PostgreSQL ይጠቀሙ፤ SQLite ን ለproduction multi-request አይመርጡ።
- [ ] Database ቋሚ storage/backup እንዳለው ያረጋግጡ።
- [ ] Production database ላይ ከመጀመር በፊት full backup ይውሰዱ፤ restore ሙከራም ያድርጉ።
- [ ] `DATABASE_URL` በhosting provider የተሰጠውን PostgreSQL connection string ይሁን።
- [ ] `Procfile` የሚጠቀመው `gunicorn backend.main:app --bind 0.0.0.0:$PORT` ነው።
- [ ] የሚሰራው `/healthz` endpoint እና የdatabase connection እንደሚሰሩ ይፈትሹ።

## 2. Environment Variables

| Variable | የሚያስፈልገው |
|---|---|
| `DATABASE_URL` | Production PostgreSQL URL — አስፈላጊ |
| `SECRET_KEY` | ረጅም፣ በrandom የተፈጠረ ሚስጥር — አስፈላጊ |
| `BOT_TOKEN` | የTelegram Bot token — Telegram login/notification ሲጠቀሙ አስፈላጊ |
| `ADMIN_IDS` | ትክክለኛ የAdmin Telegram numeric IDs፣ በcomma የተለዩ |
| `PUBLIC_URL` | የproduction HTTPS URL — Telegram webhook ለማዋቀር አስፈላጊ |
| `PHONE_VERIFY` | `on` ይቆይ፤ በproduction ላይ `off` አታድርጉ |
| `AGORA_APP_ID` | Live/voice/video ለማስኬድ |
| `AGORA_APP_CERT` | Agora certificate ከተጠቀሙ |
| `TELEBIRR_NUMBER` | ለደንበኞች የሚታየው የክፍያ ቁጥር |
| `USDT_ADDRESS` | በTRON mainnet የሚቀበለው የUSDT TRC20 wallet |
| `USDT_BIRR` | በapp ውስጥ የሚጠቀሙት የBirr/USDT ዋጋ፤ በእውነተኛ ገበያ መሠረት ይወሰን |
| `TRONGRID_API_URL` | አማራጭ፤ ነባሪው `https://api.trongrid.io` ነው |
| `TRONGRID_API_KEY` | አማራጭ የTronGrid API key፤ የrate limit ጥቅም ሊኖረው ይችላል |
| `BOT_USERNAME` | የBot username |
| `CHANNEL_URL`, `SUPPORT` | የCommunity እና Support መረጃ |
| `DEV_MODE` | Production ላይ መጥፋት/Unset መሆን አለበት |

**ሚስጥሮችን** በGitHub source code ወይም public files ውስጥ አታስቀምጡ። የdefault Admin ID በproduction ላይ በትክክለኛው `ADMIN_IDS` ይተኩ።

## 3. Telegram እና Live Integrations

- [ ] Bot token ትክክለኛ መሆኑን ያረጋግጡ።
- [ ] `PUBLIC_URL` በHTTPS እንደሚከፈት ያረጋግጡ። የphone verification flow በBot በኩል webhook ለማዋቀር ይሞክራል።
- [ ] Bot ከሌላ server ጋር እንዳልተያያዘ ያረጋግጡ።
- [ ] Agora App ID/Certificate በproduction credentials ይሁኑ።
- [ ] በAndroid ስልክ ላይ Telegram login, OTP, room join, Agora audio/video በእውነት ይፈትሹ።

## 4. Payments እና Withdrawals — የገንዘብ ደህንነት

- [ ] Telebirr አሁን manual review ነው፤ Admin የreceipt ፎቶንና reference-ን ከትክክለኛው Telebirr ገቢ ጋር በማነጻጸር ብቻ ያጽድቅ።
- [ ] TRON verifier በunit tests ተፈትሿል፤ ነገር ግን በCI ላይ እውነተኛ የUSDT transfer አልተፈተሸም። ከgo-live በፊት ትንሽ የtest transfer በmainnet ላይ በተገቢው መንገድ ማረጋገጥ እና recipient/amount/confirmations መፈተሽ ያስፈልጋል።
- [ ] Withdrawal ሲከፈል Admin የsettlement reference እና የተከፈለውን መጠን ካለ ያስገባ፤ ይህ መረጃ ብቻውን የባንክ/chain ማረጋገጫ አይደለም።
- [ ] የCOIN withdrawal liability reconciliation እና journal reconciliation ያሂዱ።
- [ ] የcoin/Birr እና USDT/Birr ዋጋዎች፣ commission rates፣ withdraw threshold እና refund policy በባለቤቱ በግልጽ ይፈቀዱ።

## 5. የDatabase እና ሂሳብ ሙከራዎች

- [ ] የSQLite regression tests እና PostgreSQL 16 regression tests ሁለቱም ማለፍ አለባቸው።
- [ ] `GET /api/fin/journal/reconcile` በAdmin session ይፈትሹ።
- [ ] `GET /api/fin/withdrawals/reconciliation` በAdmin session ይፈትሹ።
- [ ] `GET /api/fin/journal/trial-balance?currency=COIN` እና `ETB` ይፈትሹ።
- [ ] የbackup እና restore ሙከራ ያድርጉ።
- [ ] በproduction ላይ የአንድ ትንሽ የሙከራ ግዢ፣ gift፣ PK እና withdrawal workflow ከተፈቀደ በኋላ ይፈትሹ።

## 6. አሁን ያሉ ገደቦች — ከreal-money launch በፊት መፍታት ያለባቸው

1. የቆዩ የWallet እንቅስቃሴዎች፣ game/reward flows እና seller transfers ሁሉ ወደ double-entry journal አልተሻገሩም። ሙሉ wallet-to-journal reconciliation ከopening-balance እና historical migration በፊት ትክክለኛ አይሆንም።
2. የCOIN withdrawal liability በjournal ይከታተላል፤ ግን ETB/USDT የbank/chain settlement በራሱ አይመዘገብም።
3. Telebirr ራስ-ሰር ማረጋገጫ የለውም፤ manual admin verification ነው።
4. Betting/Lucky/PK እና የreal-money rewards ከመክፈት በፊት በሚመለከተው ሕግ፣ የእድሜ ገደብ እና የplatform policy ላይ የባለሙያ ግምገማ ያስፈልጋል።
5. የcoin/Birr ዋጋ፣ package price፣ withdrawal rate እና commission ፖሊሲ በአንድ የተፈቀደ ሰነድ መወሰን አለባቸው።

## 7. Release sequence

1. የባለቤቱ የproduction secrets/config ማስገባት።
2. Database backup እና schema migration በተለየ የሙከራ database ላይ ማረጋገጥ።
3. PR የመጨረሻ CI እና code review።
4. በባለቤቱ ግልጽ ፈቃድ ብቻ PR merge።
5. በባለቤቱ ግልጽ ፈቃድ ብቻ production deploy።
6. ከDeploy በኋላ smoke tests፣ logs፣ database backup እና rollback readiness።
