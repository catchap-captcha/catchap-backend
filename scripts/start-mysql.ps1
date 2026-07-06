# CatChap 로컬 MySQL 시작 스크립트 (포터블 설치 — 관리자 권한 불필요)
# 설치 위치: C:\Users\rdp-user\mysql-8.4 (MySQL 8.4.9, utf8mb4)
# DB: catchap / 계정: catchap_user / catchap_pass_2026 (root: catchap_root_2026)
# 나중에 클라우드 MySQL로 전환 시 catchap-backend\.env 의 DATABASE_URL만 교체하면 된다.

$base = "C:\Users\rdp-user\mysql-8.4"

$alive = & "$base\bin\mysqladmin.exe" -u root -pcatchap_root_2026 --port=3306 ping 2>$null
if ($alive -match 'alive') {
  Write-Output "MySQL 이미 실행 중"
} else {
  Start-Process -FilePath "$base\bin\mysqld.exe" -ArgumentList "--defaults-file=$base\my.ini", '--console' -WindowStyle Hidden
  Start-Sleep -Seconds 5
  & "$base\bin\mysqladmin.exe" -u root -pcatchap_root_2026 --port=3306 ping
}
