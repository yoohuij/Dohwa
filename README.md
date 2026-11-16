# 도화 (Dohwa)

독립적인 개인용 Discord 봇입니다. 기존 봇의 설정이나 기록을 복사하지 않습니다.

## 기본 기능

- `/핑`: 본인에게만 작동 여부와 응답 속도를 표시합니다.
- 실행할 때 슬래시 명령어를 등록합니다.
- 토큰 누락 또는 로그인 실패 시 안내를 표시합니다.

## 설정

`.env.example`을 `.env`로 복사하고 `DISCORD_TOKEN=` 뒤에 도화봇 토큰을 입력하세요.
이미 `.env`가 있다면 해당 파일을 사용하세요. 토큰과 DB는 GitHub에 올리지 않습니다.
호스팅에서는 환경 변수 `DISCORD_TOKEN`을 직접 등록할 수도 있습니다. 환경 변수가 `.env`보다 우선합니다.

## PowerShell 실행

```powershell
cd "C:\Users\유희준\Documents\Codex\2026-09-19\new-chat\Dohwa"
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bot.py
```

다음 실행부터는 해당 폴더에서 `.\.venv\Scripts\python.exe bot.py`를 사용하세요.

## 호스팅

- 저장소: `https://github.com/yoohuij/Dohwa`
- 브랜치: `main`
- 시작 파일: `bot.py`
- 패키지: `requirements.txt`로 설치
- 봇 초대 범위: `bot`, `applications.commands`

현재 기본 기능에는 Server Members Intent나 Message Content Intent가 필요하지 않습니다.

## 활동시간 자동 저장

별도의 조회 명령어는 없습니다. 음성방에 들어온 일반 유저를 서버 ID·유저 ID별로 집계합니다.

- 본인 포함 봇이 아닌 2명 이상, 마이크와 헤드셋이 켜져 있으면 활동입니다. 서버 음소거·서버 헤드셋 차단·스테이지 청중·AFK방은 음성 활동으로 인정하지 않습니다.
- 같은 서버에서 본인이 메시지를 보내면 혼자 있거나 음소거/헤드셋 차단 중이어도 활동이 재개됩니다. 음성방 밖의 채팅은 이번 집계에 포함하지 않습니다.
- 음성 활동이 없으면 시간을 보류합니다. 30분 미만에 채팅 또는 음성 활동이 재개되면 보류 시간을 포함하고, 정확히 30분 이상이면 그 구간 전체를 버립니다. 이후 재개 시점부터 다시 누적합니다.
- 음성방 퇴장 시 30분 미만의 보류 구간은 포함합니다. 봇 재시작·연결 끊김 시 미확정 보류 구간은 버리며, 오프라인 시간은 적립하지 않습니다.
- 채팅 내용이나 녹음은 저장하지 않습니다. 봇이 볼 수 있는 채널의 새 메시지만 감지합니다.
- 기본 DB: bot.py 옆 activity.sqlite3. ACTIVITY_DATABASE_PATH로 영구 저장 경로를 지정할 수 있습니다. 호스팅 업데이트/재설치 때 이 DB를 보존해야 합니다.
- SQLite activity_totals(guild_id, user_id, active_seconds)에 확정 시간을 저장하며 약 10초마다 갱신합니다. 추후 기능은 ActivityStore.total(guild_id, user_id)로 조회할 수 있습니다. 보류 시간은 조회값에 포함되지 않습니다.
- 기본 Gateway intents만 사용합니다. 채팅 내용이 필요하지 않으므로 Message Content Intent는 필요하지 않습니다.
- 한 토큰으로 프로세스 하나만 실행하세요. 갑작스러운 종료 시 마지막 저장 이후의 짧은 구간은 유실될 수 있습니다.

검증: python -m unittest discover -s . -p test_activity.py -v
