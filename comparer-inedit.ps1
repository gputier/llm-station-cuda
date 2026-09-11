param(
  [string]$Dossier = 'D:\LLM-Setup\bench\inedit'
)

# Reads every per-model journal of the inedit set and puts the models side by
# side. Two things are produced here that no single run can give.
#
# First, the paired comparison. The models answer the SAME items, so the right
# test is McNemar on the items where they disagree, not a comparison of two
# independent percentages. It is markedly more sensitive: on 95 items a gap of
# 8.5 points failed to reach significance as two separate scores AND as a paired
# test, and knowing which of the two was used is the difference between "these
# models differ" and "we cannot tell yet".
#
# Second, the per-item retention band. An item every model gets right, or every
# model gets wrong, ranks nobody. Counting them says how much of the set is
# actually doing work, which is the only honest way to decide whether the set
# needs to grow.

$ErrorActionPreference = 'Stop'

$brutDir = "$Dossier\bruts"
$modeles = @()
$juste   = @{}   # modele -> @{ id -> bool }
$famille = @{}   # id -> famille

foreach ($f in (Get-ChildItem $brutDir -Filter '*.jsonl' | Sort-Object Name)) {
  $nom = $f.BaseName
  $modeles += $nom
  $juste[$nom] = @{}
  foreach ($l in (Get-Content $f.FullName -Encoding UTF8)) {
    if (-not $l.Trim()) { continue }
    $o = ConvertFrom-Json $l
    $juste[$nom][$o.id] = ($o.verdict -eq 'juste')
    $famille[$o.id] = $o.family
  }
}

if ($modeles.Count -lt 2) { throw "il faut au moins deux modeles depouilles, trouve $($modeles.Count)" }

# Only items every model was actually asked. Scoring one model on 95 items and
# another on 94 would make the percentages incomparable without saying so.
$communs = @($famille.Keys | Where-Object { $id = $_; ($modeles | Where-Object { $juste[$_].ContainsKey($id) }).Count -eq $modeles.Count })

Write-Output ("epreuves communes aux {0} modeles : {1}" -f $modeles.Count, $communs.Count)
Write-Output ''

Write-Output 'SCORES'
foreach ($m in $modeles) {
  $n = 0
  foreach ($id in $communs) { if ($juste[$m][$id]) { $n++ } }
  $pct = [math]::Round(100.0 * $n / $communs.Count, 1)
  Write-Output ("  {0,-12} {1,3}/{2}  soit {3} %" -f $m, $n, $communs.Count, $pct)
}
Write-Output ''

Write-Output 'PAR FAMILLE'
foreach ($fam in (@($famille.Values) | Sort-Object -Unique)) {
  $ids = @($communs | Where-Object { $famille[$_] -eq $fam })
  $ligne = "  {0}  sur {1,3} epreuves : " -f $fam, $ids.Count
  foreach ($m in $modeles) {
    $n = 0
    foreach ($id in $ids) { if ($juste[$m][$id]) { $n++ } }
    $ligne += ("{0} {1}/{2}   " -f $m, $n, $ids.Count)
  }
  Write-Output $ligne
}
Write-Output ''

Write-Output 'COMPARAISONS DEUX A DEUX, test apparie de McNemar avec correction de continuite'
Write-Output '  seuil 5 pour cent : 3,84   seuil 1 pour cent : 6,63'
for ($i = 0; $i -lt $modeles.Count; $i++) {
  for ($j = $i + 1; $j -lt $modeles.Count; $j++) {
    $a = $modeles[$i]; $b = $modeles[$j]
    $n10 = 0; $n01 = 0
    foreach ($id in $communs) {
      if ($juste[$a][$id] -and -not $juste[$b][$id]) { $n10++ }
      if ($juste[$b][$id] -and -not $juste[$a][$id]) { $n01++ }
    }
    $d = $n10 + $n01
    if ($d -eq 0) {
      Write-Output ("  {0} contre {1} : aucune discordance" -f $a, $b)
      continue
    }
    $chi = [math]::Pow([math]::Abs($n10 - $n01) - 1, 2) / $d
    $verdict = if ($chi -gt 6.63) { 'ecart net' } elseif ($chi -gt 3.84) { 'ecart lisible' } else { 'INDISTINGUABLES' }
    Write-Output ("  {0,-12} contre {1,-12} : {2} pour l un, {3} pour l autre, chi2 {4}  -> {5}" -f `
      $a, $b, $n10, $n01, [math]::Round($chi, 2), $verdict)
  }
}
Write-Output ''

# An item nobody gets right and an item everybody gets right cost the same GPU
# time as a discriminating one and rank nobody. This is the figure that says
# whether the set earns its size.
$tousJustes = 0; $tousFaux = 0
foreach ($id in $communs) {
  $n = 0
  foreach ($m in $modeles) { if ($juste[$m][$id]) { $n++ } }
  if ($n -eq $modeles.Count) { $tousJustes++ }
  if ($n -eq 0) { $tousFaux++ }
}
$utiles = $communs.Count - $tousJustes - $tousFaux
Write-Output 'POUVOIR DISCRIMINANT DU JEU'
Write-Output ("  reussies par tous  : {0}" -f $tousJustes)
Write-Output ("  ratees par tous    : {0}   <- a relire, une cle fausse se cache ici" -f $tousFaux)
Write-Output ("  discriminantes     : {0} sur {1}" -f $utiles, $communs.Count)
