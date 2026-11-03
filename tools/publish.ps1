# 把 製作流程/專案看板 推到 GitHub（cynthianomail-gif/SIXSIXSIX 的 main）。
# 以遠端 main 為父 commit，並保留遠端的 data/（大家按「儲存」存的共用資料），只換掉程式檔。
# 用法（先在 SixSixSix 把這個資料夾 commit 好）：powershell -File tools\publish.ps1 -Message "說明"
param([string]$Message = '更新專案看板')
$ErrorActionPreference = 'Stop'
$repo = 'D:\demo\SixSixSix'
$url = 'https://github.com/cynthianomail-gif/SIXSIXSIX.git'
$env:GIT_TERMINAL_PROMPT = '0'
Set-Location $repo
git fetch -q $url main
$remote = (git rev-parse FETCH_HEAD).Trim()
$local = (git rev-parse 'HEAD:製作流程/專案看板').Trim()
# 用暫存 index 組新的樹：本機資料夾的檔案＋遠端的 data/
$idx = Join-Path $env:TEMP ('kanban_publish_' + [guid]::NewGuid().ToString('N') + '.idx')
$env:GIT_INDEX_FILE = $idx
try {
  git read-tree $local
  $data = git ls-tree -r $remote -- data
  foreach ($line in $data) {
    if ($line -match '^(\d+) blob ([0-9a-f]+)\t(.+)$') { git update-index --add --cacheinfo "$($Matches[1]),$($Matches[2]),$($Matches[3])" }
  }
  $tree = (git write-tree).Trim()
} finally { Remove-Item -LiteralPath $idx -ErrorAction SilentlyContinue; Remove-Item Env:GIT_INDEX_FILE }
if ((git rev-parse "$remote^{tree}").Trim() -eq $tree) { "內容和 GitHub 上一樣，不用推"; exit 0 }
$hit = git grep -n -I -e 'wanin\.tw' $tree -- . ':!tools/e2e_board.py'
if ($hit) { "發現信箱，停止：$hit"; exit 1 }
$msg = $Message + "`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
$c = (git commit-tree $tree -p $remote -m $msg).Trim()
git push -q $url "${c}:refs/heads/main"
"已推上 GitHub：$c（父：$remote，保留 data/ $($data.Count) 個檔）"
