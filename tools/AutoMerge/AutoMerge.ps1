param([switch]$Inspect, [switch]$Test)
$ErrorActionPreference='Stop'
$mutex=New-Object System.Threading.Mutex($false,'Local\SIRO_TFT_PreviewAutoMerge')
$ownsMutex=$false
try {
 if(!$Inspect -and !$Test) {
  try {$ownsMutex=$mutex.WaitOne(0,$false)} catch [System.Threading.AbandonedMutexException] {$ownsMutex=$true}
  if(!$ownsMutex) {exit}
 }
 Add-Type -AssemblyName Accessibility,System.Windows.Forms,System.Drawing,System.Xml.Linq
 $code=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'AutoMerge.cs'),[Text.Encoding]::UTF8)
 if($Test) {$code += [Environment]::NewLine + [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'AutoMerge.Tests.cs'),[Text.Encoding]::UTF8)}
 Add-Type -TypeDefinition $code -ReferencedAssemblies Accessibility,System.Windows.Forms,System.Drawing,System.Core,System.Xml,System.Xml.Linq
 if($Inspect) {[TftPreview.Entry]::Inspect();exit}
 if($Test) {[TftPreview.Tests]::Run();exit}
 [TftPreview.Entry]::Run((Join-Path $PSScriptRoot 'AutoMerge.log'))
} finally {if($ownsMutex) {$mutex.ReleaseMutex()};$mutex.Dispose()}
