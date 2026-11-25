# PPT - IMG Converter

AppDash에 연결할 수 있는 로컬 PPT·이미지 변환 앱입니다. 기존 PDF - IMG Converter의 UI를 바탕으로 만들었습니다. 외부 변환 API 없이 앱을 실행하는 PC에서 처리합니다.

## 실행

처음 설치하는 경우 PowerShell에서:

```powershell
git clone https://github.com/cjaehyuk4zed/Appdash-PPT-Converter.git
cd Appdash-PPT-Converter
.\setup.ps1
.\start.ps1
```

- 접속: <http://127.0.0.1:8766/>
- 상태 확인: <http://127.0.0.1:8766/api/ready>
- 종료: 실행한 터미널에서 `Ctrl+C`, 또는 AppDash에서 중지
- 새 PC 또는 의존성 재설치: `.\setup.ps1`
- 설치가 끝난 기존 폴더에서는 `.\start.ps1`만 실행하면 됩니다.
- Python 3.14 권장. Windows에서는 Microsoft PowerPoint 또는 LibreOffice가 필요합니다. Linux에서는 LibreOffice Impress가 필요합니다.
- `CONVERTER_SOFFICE`에 `soffice` 실행 파일 경로를 지정할 수 있습니다. LibreOffice가 있으면 우선 사용하고, 없으면 Windows PowerPoint를 사용합니다.
- PowerPoint는 로그인·초기 설정·제품 인증이 완료되어 있어야 합니다. 무인 Windows 서비스 계정보다는 로그인한 사용자 계정에서 실행하세요.

## 기능

| 메뉴 | 기능 |
|---|---|
| PPT → 이미지 | `.ppt`, `.pptx` 슬라이드를 JPG, PNG, WebP, TIFF, BMP, AVIF로 변환 |
| 이미지 → PPT | 이미지들을 하나의 PPTX로 합치거나 이미지마다 별도 PPTX 생성 |
| 이미지 변환 | 일괄 형식 변환, 비율 유지 크기 조절, 색상·배경·EXIF 설정 |
| PPT 압축 | 편집 가능한 내용을 유지하는 구조 최적화, 또는 슬라이드를 이미지로 만드는 압축 |

이미지 입력은 JPEG, PNG, WebP, TIFF, BMP, GIF, AVIF, HEIC/HEIF를 지원합니다. TIFF는 여러 프레임을 가져오며 GIF 등은 첫 프레임만 사용합니다. 코덱 지원 상태는 `/api/ready`에서 확인할 수 있습니다.

- 한국어 기본, English 전환 및 브라우저별 언어 저장. 파일·설정·결과를 유지하며 전환합니다.
- 슬라이드·이미지를 썸네일 카드 목록으로 표시하며, 핸들을 마우스·터치로 드래그해 순서를 바꿉니다. 삽입 위치 표시와 목록 가장자리 자동 스크롤을 지원합니다.
- 선택·회전 상태를 유지하며 이동합니다. 핸들의 ↑/↓ 키 또는 카드의 이동 버튼으로도 순서를 바꿀 수 있습니다. 변환·업로드 중에는 변경이 잠깁니다.
- 카드별 선택, 90도 회전, 파일 삭제, PPT 슬라이드 범위 지정을 지원합니다. 여러 슬라이드·페이지가 있는 파일의 X 버튼은 해당 파일 전체를 삭제합니다.
- 화질 선택과 용량 선택. 이미지 출력 기본 제한은 **1 MB**, PPTX 출력은 **10 MB**입니다.
- 용량 선택에서는 화질 **1~95**를 자동 탐색합니다. 허용한 범위에서 해상도를 낮출 수 있습니다.
- 용량은 ZIP 전체가 아닌 **개별 결과 파일의 실제 바이트 크기**로 확인합니다. `1 KB = 1,000 bytes`, `1 MB = 1,000,000 bytes`입니다.
- 탐색 횟수는 제한되어 있으며 최적 인코딩을 수학적으로 보장하지 않습니다. 제한을 맞추지 못하면 가장 작은 시도 결과와 ‘목표 용량 미달성’을 표시합니다.
- 기본적으로 접힌 ‘이미지 크기·해상도’ 설정, 현재 값 요약, 최대 너비·높이, DPI.
- 첫 번째 선택 슬라이드·이미지 미리보기, 개별 파일·ZIP 다운로드, 작업 취소·결과 삭제.
- 결과 파일명은 `원본파일명_001.jpg`, `원본파일명_002.jpg`처럼 확장자 앞에만 세 자리 순번을 붙입니다. 번호는 변환 결과 순서이며, ZIP 안에서도 같은 파일명을 사용합니다.
- 원본을 덮어쓰지 않으며 업로드·중간 렌더링·결과는 기본적으로 `.runtime`에 보관합니다. 보관 시간은 1시간입니다. 새로고침하면 화면의 파일 목록이 초기화됩니다.

## PPT 관련 제한

- PPTX 생성 결과는 **슬라이드마다 이미지 한 장**을 넣습니다. 이미지의 글자를 편집 가능한 텍스트로 복원하지 않습니다.
- 하나의 PPTX는 공통 슬라이드 크기를 사용합니다. ‘첫 이미지 비율에 맞춤’, 16:9, 4:3, A4, Letter를 선택할 수 있으며 나머지 이미지는 여백 안에 비율을 유지해 배치합니다.
- PPT 렌더링은 정지 화면입니다. 애니메이션 재생·전환·오디오·비디오는 이미지에 포함하지 않습니다. 숨김 슬라이드도 원래 번호대로 가져옵니다.
- 이미지 방식 PPT 압축은 편집 가능한 텍스트·도형, 링크, 애니메이션·미디어를 제거합니다.
- 내용 유지 압축은 PPTX 패키지를 다시 저장·압축하며 이미지 해상도를 낮추지 않습니다. 용량이 줄지 않을 수 있습니다. 슬라이드 선택·순서 변경은 지원하되, 회전하려면 이미지 압축 방식을 선택해야 합니다.
- 내용 유지 압축은 python-pptx를 사용합니다. 일반적인 텍스트·도형·이미지는 유지하지만 고급 Office 기능·추가 기능의 완전한 보존은 보장하지 않습니다. 중요한 파일은 결과를 PowerPoint에서 확인하세요. 전자서명은 무효화됩니다.
- `.ppt` 입력은 PowerPoint 또는 LibreOffice에서 `.pptx`로 정규화합니다. 결과 PPT는 모두 `.pptx`입니다.
- 암호가 설정된 파일, `.pptm` 등 매크로 포함 형식은 지원하지 않습니다. 레거시 `.ppt`를 열 때도 PowerPoint 매크로 실행을 차단합니다.
- 렌더링 결과는 설치된 글꼴과 Office 엔진에 따라 달라질 수 있습니다. Linux/Docker에서는 필요한 글꼴을 추가 설치하세요.
- PowerPoint는 앱이 연 업로드 복사본만 닫으며 사용자가 연 PowerPoint 전체를 종료하지 않습니다. Office 자체가 응답하지 않아 검사 시간 제한을 넘긴 경우, 해당 임시 문서가 PowerPoint에 남을 수 있습니다.

## AppDash 연결

`appdash-gateway-config.json`을 AppDash의 **앱 만들기 · 연결하기**에서 가져와 명령과 경로를 확인한 뒤 등록하세요. 유형은 `service`, 그룹은 `Utilities`입니다. PDF 앱의 기본 포트 `8765`와 별도로 `8766`을 사용합니다.

등록 JSON에는 현재 PC의 절대 경로가 들어 있습니다. 폴더를 옮기거나 포트를 변경하면 JSON의 실행 경로·workingDirectory·openUrl·healthUrl도 함께 수정하세요.

## 설정

| 환경 변수 | 기본값 |
|---|---|
| `CONVERTER_HOST` | `127.0.0.1` |
| `CONVERTER_PORT` | `8766` |
| `CONVERTER_DATA_DIR` | 앱 폴더의 `.runtime` |
| `CONVERTER_MAX_FILE_MB` / `CONVERTER_MAX_BATCH_MB` | `100` / `250` |
| `CONVERTER_MAX_PAGES` | `500` |
| `CONVERTER_MAX_PIXELS` | `60000000` |
| `CONVERTER_MAX_OUTPUT_MB` | `500` (ZIP 전 결과 파일 합계) |
| `CONVERTER_JOB_TIMEOUT` | `600`초 (파일 검사는 최대 180초) |
| `CONVERTER_RETENTION_SECONDS` | `3600`초 |
| `CONVERTER_ALLOWED_HOSTS` | `127.0.0.1,localhost,[::1]` |

기본 접속은 루프백으로 제한됩니다. 외부 프록시를 설정하는 경우 `CONVERTER_ALLOWED_HOSTS`, `CONVERTER_PUBLIC_ORIGINS`, `CONVERTER_SECURE_COOKIE`도 배포 환경에 맞게 설정해야 합니다. 업로드와 결과는 브라우저 세션별로 분리됩니다. 이 앱 자체는 사용자 계정 로그인 기능을 제공하지 않습니다.

## Linux / Docker

```sh
./setup.sh
.venv/bin/python run.py
# 또는
docker compose up --build -d
docker compose down
```

Dockerfile에는 LibreOffice Impress와 Noto CJK 글꼴을 포함했습니다. Linux 직접 실행 시에는 운영체제 패키지 관리자로 `libreoffice-impress` 및 필요한 글꼴을 먼저 설치하세요. Linux와 Docker 경로는 제공하지만 이 PC에서는 실행 검증하지 않았습니다.

## 검증

```powershell
.venv\Scripts\python.exe -m pytest -q
node --test tests/i18n.test.cjs tests/ui-settings.test.cjs
```

이미지 코덱·용량 제한·메타데이터·PPTX 구성, 실제 PowerPoint의 PPT/PPTX 렌더링 및 숨김 슬라이드, HTTP 업로드·변환·다운로드·삭제·세션 분리·취소·보관 정리, 서버 시작·종료를 확인하는 테스트가 있습니다. PowerPoint/LibreOffice가 없는 환경에서는 실제 렌더링 테스트를 건너뜁니다.

## 구성

Starlette + Uvicorn, HTML/CSS/JavaScript, Pillow, pypdfium2, python-pptx, pillow-heif, Windows COM(pywin32) 또는 LibreOffice를 사용합니다. PowerPoint는 업로드 시 임시 PDF로 렌더링하고 이후 이미지 변환 작업은 분리된 Python 프로세스에서 수행합니다. 프런트엔드 빌드나 CDN은 필요하지 않습니다.
