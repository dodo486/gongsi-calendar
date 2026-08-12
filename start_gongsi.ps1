# 공시캘린더 아침 자동기동 런처 (작업 스케줄러가 매일 08:00 실행)
# serve.py(대시보드) + monitor.py(폴러·토스트)를 안 떠 있을 때만 켠다(멱등).
$py  = "C:\Users\NHWM\AppData\Local\Programs\Python\Python312\python.exe"
$dir = "D:\공시캘린더"
function Ensure-Running($script) {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -like "*$script*" }
    if ($p) { return }
    Start-Process -FilePath $py -ArgumentList $script -WorkingDirectory $dir -WindowStyle Hidden
}
Ensure-Running "serve.py"
Start-Sleep -Milliseconds 1000
Ensure-Running "monitor.py"
