param(
  [int]$Port = 8080,
  [int]$GpuLayers = 99,
  [int]$CtxSize = 32768
)

$amigoRoot = Split-Path -Parent $PSScriptRoot
$server = Join-Path $amigoRoot "amigo\lib\llama\llama-server.exe"
$model  = Join-Path $amigoRoot "models\LFM3-Omni-2B-Q4_K_M.gguf"

if (-not (Test-Path $server)) { throw "Missing llama-server.exe at $server" }
if (-not (Test-Path $model))  { throw "Missing LFM3 model at $model" }

Write-Host "Starting LFM3-Omni-2B offline server on http://127.0.0.1:$Port/v1 ..."
& $server `
  -m $model `
  --host 127.0.0.1 `
  --port $Port `
  -c $CtxSize `
  -ngl $GpuLayers `
  -np 1 `--jinja
