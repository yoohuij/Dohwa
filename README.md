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
