# 릴스 캡션 생성기 개발 파일

이 폴더에는 `릴스 캡션 생성기.exe`를 만들기 위한 Python 소스, 테스트, 빌드 스크립트가 들어 있습니다.

## 로컬 실행

```powershell
.\run_app.ps1
```

## 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 빌드

```powershell
.\build.ps1
```

빌드 스크립트는 다음 작업을 순서대로 수행합니다.

1. 가상환경 생성 및 의존성 설치
2. 테스트 실행
3. PyInstaller onedir 빌드
4. 루트 폴더의 `릴스 캡션 생성기.exe`와 `프로그램 구성 파일` 런타임 갱신
5. Tcl/Tk 데이터 폴더 보강

## GitHub 포함 정책

- 포함: 실행 파일, 런타임 파일, 소스 코드, 테스트, 작은 학습용 `캡션.txt`/`스크립트.txt`
- 제외: `.venv`, `build`, `dist`, 생성 결과물, 학습용 영상, 스크린샷 이미지
