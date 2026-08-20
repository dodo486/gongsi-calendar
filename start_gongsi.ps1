# 공시캘린더 자동기동 + 창감시 런처 (예약작업: 로그인·잠금해제 시 + 5분마다 실행)
#  1) serve.py(대시보드)·monitor.py(폴러·토스트) 상주 보장(안 떠 있으면 기동)
#  2) 크롬이 띄운 대시보드 앱창이 없으면 → 크롬 --app 으로 오픈(닫으면 다음 주기에 재오픈)
$py     = "C:\Users\NHWM\AppData\Local\Programs\Python\Python312\python.exe"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$dir    = "D:\공시캘린더"
$url    = "http://127.0.0.1:8777/"
$titleKey = "공시캘린더"   # 페이지 <title> = "📅 공시캘린더"

function Running($script) {
    [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
           Where-Object { $_.CommandLine -like "*$script*" })
}
function Ensure($script) {
    if (Running $script) { return }
    Start-Process -FilePath $py -ArgumentList $script -WorkingDirectory $dir -WindowStyle Hidden
}
Ensure "serve.py"
Ensure "monitor.py"

# serve 포트가 응답할 때까지 대기(방금 켰다면 로딩 시간 필요)
for ($i = 0; $i -lt 40; $i++) {
    try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null; break }
    catch { Start-Sleep -Milliseconds 700 }
}

# 크롬이 소유한 '공시캘린더' 앱창이 떠 있는지 탐지(엣지·일반탭 제외)
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class Win32 {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
}
"@
$script:found = $false
$cb = [Win32+EnumWindowsProc]{ param($h,$l)
    if (-not [Win32]::IsWindowVisible($h)) { return $true }
    $sb = New-Object System.Text.StringBuilder 512
    [Win32]::GetWindowText($h,$sb,512) | Out-Null
    $t = $sb.ToString()
    if ($t -like "*$titleKey*") {
        $wpid = 0
        [Win32]::GetWindowThreadProcessId($h,[ref]$wpid) | Out-Null
        $p = Get-Process -Id $wpid -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -eq 'chrome') { $script:found = $true }
    }
    return $true
}
[Win32]::EnumWindows($cb,[IntPtr]::Zero) | Out-Null

if (-not $script:found -and (Test-Path $chrome)) {
    Start-Process -FilePath $chrome -ArgumentList "--app=$url"   # 크롬 앱창으로 대시보드 오픈
}
