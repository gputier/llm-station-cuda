param(
  [Parameter(Mandatory=$true)][string]$Label,
  [string]$Dossier = 'D:\LLM-Setup\bench\inedit',
  [string]$Uri     = 'http://127.0.0.1:8080/v1/chat/completions',
  # Sampling belongs to the served profile, not to this bench: these are the
  # values the existing quality bench uses, kept identical so the two are read
  # side by side. Temperature 0 HERE and nowhere else, a serving profile never
  # does greedy decoding.
  [double]$Temperature = 0,
  [int]$TopK = 20,
  [double]$TopP = 0.95,
  # Run the corrector against synthetic answers with known verdicts, contact no
  # model, and exit. A corrector that has never been shown a wrong answer is
  # not known to reject one.
  [switch]$SelfTest,
  # Rebuild the report from the answers already on disk, contacting no model.
  # Two uses, and both were paid for before this existed. It re-scores a run
  # whose corrector was wrong without spending the GPU time again, and it is the
  # only way to exercise the reporting phase, which no run had ever reached.
  [switch]$Depouiller,
  # Les trois surcharges qui servent a repondre a une seule question : est-ce le
  # MODELE que je mesure, ou mon reglage ? Elles ne changent rien par defaut, et
  # une passe qui en emploie une n'est jamais comparable a une passe normale.
  # Elles existent parce qu'un banc dont on ne peut pas bouger un parametre ne
  # sait pas distinguer une faiblesse d'un brida.
  # Un tableau et non une chaine : PowerShell lit "F1,F2" sans guillemets comme
  # une LISTE, et un parametre [string] refuse alors la valeur avec un message
  # qui parle de transformation d'argument et non de guillemets manquants. En
  # acceptant les deux formes, l'appel marche quelle que soit la ponctuation.
  [string[]]$Familles = @(),
  [int]$MaxTokens = 0,
  [double]$PresencePenalty = -1,
  # Laisser le modele reflechir. Le banc public coupe la reflexion, et c'est
  # justifie la-bas : il pose des questions a choix multiples ou une chaine de
  # raisonnement ne sert qu'a couter du temps.
  #
  # Ici cela fausse tout, et la mesure du 11/09/2026 le montre sans appel.
  # Longueur mediane de la reponse sur la famille de reecriture, meme protocole
  # pour les quatre : oxcoder 1943 caracteres, granite 6230, neohorse 12,
  # ling 15. Les deux premiers IGNORENT l'option et raisonnent quand meme, les
  # deux derniers l'HONORENT et repondent au jugé en une douzaine de caracteres.
  #
  # Le protocole est donc identique sur le papier et inegal dans les faits : il
  # compare des modeles qui reflechissent a des modeles a qui on a interdit de
  # reflechir, et le classement mesure en partie l'obeissance a cette option.
  # Sur un jeu de raisonnement, la reflexion se laisse active.
  [switch]$Reflexion
)

# Bench for the ORIGINAL test set, the one nobody outside this house has seen.
#
# It is a separate script from banc.ps1 on purpose. That one reads a fixed pair
# of shapes, multiple choice and a final integer. This one reads five correction
# modes declared per item, and it reads one thing banc.ps1 has no notion of: the
# LURE.
#
# The lure is what makes this set worth writing. On an anchored item, derived
# from a classic whose wording was altered, two values are known in advance: the
# right answer to the new wording, and the answer a model produces by reciting
# the classic it memorised. Producing the second is not an arithmetic slip, it
# is a signature, and four of them out of sixty carry more weight than any
# score gap. So lure hits are counted and reported SEPARATELY, and they are not
# a third outcome: a lure hit is a wrong answer that also tells us why.
#
# Schema of the items: scratchpad/SCHEMA-epreuves.md, which this file implements
# and must not drift from.

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# Text normalisation, applied to both sides before any comparison. Deliberately
# tolerant: a strict format measures obedience to the format, not the capacity
# the item is about.
function Normalise($s) {
  if ($null -eq $s) { return '' }
  $t = [string]$s
  $t = $t.ToLowerInvariant()
  # Accents off via canonical decomposition, so "élève" and "eleve" compare equal.
  $t = [string]::Join('', ($t.Normalize([Text.NormalizationForm]::FormD).ToCharArray() |
        Where-Object { [Globalization.CharUnicodeInfo]::GetUnicodeCategory($_) -ne 'NonSpacingMark' }))
  $t = $t -replace '\s+', ' '
  $t = $t.Trim()
  $t = $t -replace '[\.,;:!\?]+$', ''
  return $t
}

# Number normalisation. "35000", "35 000", "35.000" and "35 000" with a
# non-breaking space are the same number, and a model that found the
# contradiction must not be marked wrong on how it writes digits. That would
# measure typography.
function NormaliseNombre($s) {
  if ($null -eq $s) { return '' }
  $t = [string]$s
  $t = $t -replace '[\s\u00A0\u202F]', ''
  $t = $t -replace ',', '.'
  return $t.ToLowerInvariant()
}

# Thousand separators removed IN PLACE, the rest of the sentence untouched, so
# that a reply can be read a second time as if the model had written its numbers
# unspaced. Separately from NormaliseNombre because this one runs on a whole
# sentence before the numbers are pulled out of it: stripping afterwards is too
# late, the regex has already cut "35 000" into 35 and 000.
function StripMilliers($s) {
  if ($null -eq $s) { return '' }
  $t = [string]$s
  for ($i = 0; $i -lt 4; $i++) {
    $t = $t -replace '(\d)[\s\u00A0\u202F\.](\d{3})(?!\d)', '$1$2'
  }
  return $t
}

# The model's verdict line. The LAST "Answer:" wins, searched in the last three
# non-empty lines, because a reasoning model names candidates along the way and
# only its closing line is its answer.
function LireReponse($texte) {
  if ([string]::IsNullOrWhiteSpace($texte)) { return $null }
  $lignes = @($texte -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
  if ($lignes.Count -eq 0) { return $null }
  $depart = [Math]::Max(0, $lignes.Count - 3)
  for ($i = $lignes.Count - 1; $i -ge $depart; $i--) {
    $m = [regex]::Match($lignes[$i], '(?i)answer\s*[:：]\s*(.+)$')
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
  }
  return $null
}

function ExtraireNombres($s, $motif) {
  if ($motif) {
    $m = [regex]::Match($s, $motif)
    if (-not $m.Success) { return @() }
    $vals = @()
    for ($g = 1; $g -lt $m.Groups.Count; $g++) { $vals += $m.Groups[$g].Value }
    if ($vals.Count -eq 0) { $vals = @($m.Value) }
    return $vals
  }
  return @([regex]::Matches($s, '-?\d+(?:[\.,]\d+)?') | ForEach-Object { $_.Value })
}

# ---------------------------------------------------------------------------
# The five correction modes, and no others
# ---------------------------------------------------------------------------
function Corriger($epreuve, $brut) {
  $rep = LireReponse $brut
  # No Answer: line at all, truncation included, is VIDE and never faux. The
  # count of vides is read BEFORE the score.
  if ($null -eq $rep) { return @{ verdict = 'vide'; lu = $null } }

  $check = $epreuve.check
  $type  = [string]$check.type

  switch ($type) {

    'exact_norm' {
      $ok = (Normalise $rep) -eq (Normalise $epreuve.answer)
      return @{ verdict = if ($ok) { 'juste' } else { 'faux' }; lu = $rep }
    }

    'numeric' {
      $motif = if ($check.PSObject.Properties.Name -contains 'pattern') { [string]$check.pattern } else { $null }
      $tol   = if ($check.PSObject.Properties.Name -contains 'tolerance') { [double]$check.tolerance } else { 0 }
      # BOTH sides are read the same way. Where a pattern is declared it applies
      # to the expected answer too: the writers state it in the shape the item
      # demands, "A=7, B=3, C=6", never as a bare list of numbers. Reading only
      # the reply through the pattern left [double] to choke on that string, and
      # killed two full runs at the first F2 item with no line in the log.
      $attendus = @(ExtraireNombres ([string]$epreuve.answer) $motif)
      if ($attendus.Count -eq 0) { $attendus = @($epreuve.answer) }
      # Two readings of the same reply, and either one passing is enough.
      # "35 000" and "35.000" are one number to a human and three tokens to a
      # regex, so the thousand separators are stripped in the second reading.
      # Trying both rather than choosing keeps "1,234" readable as 1.234 where
      # that is what the model meant. The tolerance runs in the direction of not
      # failing a correct model on its typography.
      foreach ($variante in @($rep, (StripMilliers $rep))) {
        $lus = @(ExtraireNombres $variante $motif)
        if ($lus.Count -lt $attendus.Count) { continue }
        $ok = $true
        for ($i = 0; $i -lt $attendus.Count; $i++) {
          $a = [double](NormaliseNombre $attendus[$i])
          $b = [double](NormaliseNombre $lus[$i])
          if ([Math]::Abs($a - $b) -gt $tol) { $ok = $false; break }
        }
        if ($ok) { return @{ verdict = 'juste'; lu = $rep } }
      }
      return @{ verdict = 'faux'; lu = $rep }
    }

    'set' {
      $att = @($epreuve.answer | ForEach-Object { Normalise $_ }) | Sort-Object -Unique
      $lus = @($rep -split '[,;]' | ForEach-Object { Normalise $_ } | Where-Object { $_ -ne '' }) | Sort-Object -Unique
      $ok = ($att.Count -eq $lus.Count) -and (-not (Compare-Object $att $lus))
      return @{ verdict = if ($ok) { 'juste' } else { 'faux' }; lu = $rep }
    }

    'impossible' {
      # The IMPOSSIBLE token AND every element the item demands be cited with
      # it. Naming the verdict without naming what contradicts is not a
      # detection, it is a guess that landed.
      if ((Normalise $rep) -notmatch 'impossible') { return @{ verdict = 'faux'; lu = $rep } }
      $norm = NormaliseNombre (Normalise $rep)
      foreach ($el in @($check.require_elements)) {
        if ($norm -notmatch [regex]::Escape((NormaliseNombre (Normalise $el)))) {
          return @{ verdict = 'faux'; lu = $rep }
        }
      }
      return @{ verdict = 'juste'; lu = $rep }
    }

    'constraints' {
      # Conjunctive: every predicate true or the item is failed. Grammaticality
      # is NEVER scored, no script decides it, and the wording says so to the
      # model. Predicates are structured, never source to evaluate.
      $phrase = $rep
      $mots = @($phrase -split '\s+' | Where-Object { $_ -ne '' })
      foreach ($r in @($check.rules)) {
        switch ([string]$r.pred) {
          'word_count'      { if ($mots.Count -ne [int]$r.value) { return @{ verdict='faux'; lu=$rep } } }
          'word_at'         { $i = [int]$r.index - 1
                              if ($i -lt 0 -or $i -ge $mots.Count) { return @{ verdict='faux'; lu=$rep } }
                              if ((Normalise $mots[$i]) -ne (Normalise $r.value)) { return @{ verdict='faux'; lu=$rep } } }
          'no_repeat'       { $u = @($mots | ForEach-Object { Normalise $_ }) | Sort-Object -Unique
                              if ($u.Count -ne $mots.Count) { return @{ verdict='faux'; lu=$rep } } }
          'max_word_len'    { foreach ($m in $mots) { if ($m.Length -gt [int]$r.value) { return @{ verdict='faux'; lu=$rep } } } }
          'forbidden_chars' { foreach ($c in ([string]$r.value).ToCharArray()) {
                                if ($phrase.Contains($c)) { return @{ verdict='faux'; lu=$rep } } } }
          default           { throw "predicat inconnu : $($r.pred) sur $($epreuve.id)" }
        }
      }
      return @{ verdict = 'juste'; lu = $rep }
    }

    default { throw "mode de correction inconnu : '$type' sur $($epreuve.id)" }
  }
}

# Did the model produce the memorised answer rather than the one this wording
# calls for? Only meaningful on items that declare a lure.
function EstLeurre($epreuve, $lu) {
  if ($null -eq $lu) { return $false }
  if (-not ($epreuve.PSObject.Properties.Name -contains 'lure')) { return $false }
  $leurre = NormaliseNombre (Normalise $epreuve.lure)
  $rep    = NormaliseNombre (Normalise $lu)
  if ($rep -eq $leurre) { return $true }
  # A lure stated inside a sentence still counts, but only on a number boundary,
  # so that 5 does not match inside 45.
  return $rep -match ('(^|[^0-9\.])' + [regex]::Escape($leurre) + '($|[^0-9\.])')
}

# ---------------------------------------------------------------------------
# Self test. Runs the corrector on answers whose verdict is known, so that a
# corrector change that starts accepting everything is caught here and not
# after a four hour run.
# ---------------------------------------------------------------------------
if ($SelfTest) {
  $cas = @(
    @{ nom='exact juste';        ep=@{id='t1';check=@{type='exact_norm'};answer='CBB'};                                  rep="blah`nAnswer: cbb";            attendu='juste' }
    @{ nom='exact accent';       ep=@{id='t2';check=@{type='exact_norm'};answer='élève'};                                rep="Answer: Eleve.";               attendu='juste' }
    @{ nom='exact faux';         ep=@{id='t3';check=@{type='exact_norm'};answer='CBB'};                                  rep="Answer: CBA";                  attendu='faux'  }
    @{ nom='vide sans Answer';   ep=@{id='t4';check=@{type='exact_norm'};answer='CBB'};                                  rep="je reflechis et je suis coupe"; attendu='vide'  }
    @{ nom='vide sortie nulle';  ep=@{id='t5';check=@{type='exact_norm'};answer='CBB'};                                  rep='';                             attendu='vide'  }
    @{ nom='dernier Answer';     ep=@{id='t6';check=@{type='exact_norm'};answer='B'};                                    rep="Answer: A`npardon`nAnswer: B";  attendu='juste' }
    @{ nom='num multi juste';    ep=@{id='t7';check=@{type='numeric';pattern='A=(-?\d+).*B=(-?\d+)';tolerance=0};answer=@(15,15)}; rep="Answer: A=15, B=15";  attendu='juste' }
    @{ nom='num multi faux';     ep=@{id='t8';check=@{type='numeric';pattern='A=(-?\d+).*B=(-?\d+)';tolerance=0};answer=@(15,15)}; rep="Answer: A=15, B=14";  attendu='faux'  }
    # The shape the writers actually use on F2 and F7: the expected answer is
    # spelled the way the wording demands it, not as a list of numbers. The two
    # cases above pass an array and were the only numeric+pattern coverage, so
    # the corrector shipped never having seen the real shape and died on it.
    @{ nom='num answer texte';   ep=@{id='t7b';check=@{type='numeric';pattern='A=(-?\d+).*B=(-?\d+).*C=(-?\d+)';tolerance=0};answer='A=7, B=3, C=6'}; rep="Answer: A=7, B=3, C=6"; attendu='juste' }
    @{ nom='num answer texte f'; ep=@{id='t7c';check=@{type='numeric';pattern='A=(-?\d+).*B=(-?\d+).*C=(-?\d+)';tolerance=0};answer='A=7, B=3, C=6'}; rep="Answer: A=7, B=3, C=5"; attendu='faux'  }
    @{ nom='num tolerance';     ep=@{id='t9';check=@{type='numeric';tolerance=0.005};answer=0.45};                      rep="Answer: 0,45";                 attendu='juste' }
    @{ nom='num espace mille';   ep=@{id='t10';check=@{type='numeric';tolerance=0};answer=35000};                        rep="Answer: 35 000";               attendu='juste' }
    @{ nom='impossible juste';   ep=@{id='t11';check=@{type='impossible';require_elements=@('55','50')};answer='IMPOSSIBLE'}; rep="Answer: IMPOSSIBLE (55 pour 50 membres)"; attendu='juste' }
    @{ nom='impossible nu';      ep=@{id='t12';check=@{type='impossible';require_elements=@('55','50')};answer='IMPOSSIBLE'}; rep="Answer: IMPOSSIBLE";      attendu='faux'  }
    @{ nom='impossible evite';   ep=@{id='t13';check=@{type='impossible';require_elements=@('55','50')};answer='IMPOSSIBLE'}; rep="Answer: 5";               attendu='faux'  }
    @{ nom='soluble pas impo';   ep=@{id='t14';check=@{type='numeric';tolerance=0};answer=12};                           rep="Answer: IMPOSSIBLE";           attendu='faux'  }
    @{ nom='set desordre';       ep=@{id='t15';check=@{type='set'};answer=@('cube','sphere','cone')};                    rep="Answer: cone, Cube, sphère";   attendu='juste' }
    @{ nom='set incomplet';      ep=@{id='t16';check=@{type='set'};answer=@('cube','sphere','cone')};                    rep="Answer: cube, sphere";         attendu='faux'  }
  )
  $ko = 0
  foreach ($c in $cas) {
    $ep = [pscustomobject]$c.ep
    $ep.check = [pscustomobject]$c.ep.check
    $r = Corriger $ep $c.rep
    $etat = if ($r.verdict -eq $c.attendu) { 'ok  ' } else { $ko++; 'ECHEC' }
    Write-Output ("{0} {1,-20} attendu={2,-6} obtenu={3}" -f $etat, $c.nom, $c.attendu, $r.verdict)
  }

  # Lure detection, checked on both sides: it must fire on the memorised answer
  # and stay silent on the right one, including when the lure's digits appear
  # inside a larger number.
  $ancre = [pscustomobject]@{ id='t17'; check=[pscustomobject]@{type='numeric';tolerance=0.005}; answer=0.45; lure=0.05 }
  foreach ($t in @(@{r='0,05';att=$true}, @{r='0,45';att=$false}, @{r='1,05';att=$false})) {
    $got = EstLeurre $ancre $t.r
    $etat = if ($got -eq $t.att) { 'ok  ' } else { $ko++; 'ECHEC' }
    Write-Output ("{0} leurre sur {1,-6} attendu={2,-6} obtenu={3}" -f $etat, $t.r, $t.att, $got)
  }

  Write-Output ''
  if ($ko -eq 0) { Write-Output 'AUTOTEST OK, 21 cas' } else { Write-Output "AUTOTEST ECHEC, $ko cas"; exit 1 }
  exit 0
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
# -Encoding UTF8 is NOT optional and nothing here works without it. Windows
# PowerShell 5.1 reads a file with no byte order mark as ANSI, so every accented
# character of a French wording arrives mangled at the model: "regles de
# reecriture" comes through as "rǸǸcriture". Measured on this machine on
# 2026-09-11. The older bench never showed the fault because its question set is
# in English.
$epreuves = @()
foreach ($f in (Get-ChildItem $Dossier -Filter '*.jsonl' | Sort-Object Name)) {
  foreach ($l in (Get-Content $f.FullName -Encoding UTF8)) {
    if ($l.Trim()) { $epreuves += (ConvertFrom-Json $l) }
  }
}
if ($Familles.Count -gt 0) {
  # Aplati, de sorte que -Familles F1,F2 et -Familles 'F1,F2' donnent le meme
  # resultat.
  $voulues = @($Familles | ForEach-Object { $_ -split ',' } |
               ForEach-Object { $_.Trim().ToUpperInvariant() } | Where-Object { $_ })
  $epreuves = @($epreuves | Where-Object { $voulues -contains [string]$_.family })
  Write-Output ("filtre sur {0} : {1} epreuves retenues" -f ($voulues -join ','), $epreuves.Count)
}
if ($MaxTokens -gt 0) {
  foreach ($e in $epreuves) { $e.max_tokens = $MaxTokens }
  Write-Output ("plafond de sortie force a {0} jetons" -f $MaxTokens)
}
if ($PresencePenalty -ge 0) {
  Write-Output ("penalite de presence forcee a {0}" -f $PresencePenalty)
}
if ($epreuves.Count -eq 0) { throw "aucune epreuve trouvee dans $Dossier" }

# Dry run of the whole set before a single token of GPU time is spent. Each item
# is corrected against its own expected answer, and the only thing checked is
# that correcting it does not throw. It costs nothing and it is what the first
# campaign lacked: two models were served for nothing because item 25 carried a
# fault that only showed once the model had already answered it.
$malformees = @()
foreach ($e in $epreuves) {
  try {
    $v = Corriger $e ("Answer: " + ($e.answer -join ', '))
    # The expected answer must pass its own correction. An item whose key fails
    # its own check can never be answered by anyone, and on a set nobody outside
    # this house has seen there is no third party to contradict it. The one
    # exception is the impossible mode, whose key is the bare word while the
    # check also demands the contradictory figures be cited.
    if ($e.check.type -ne 'impossible' -and $v.verdict -ne 'juste') {
      $malformees += ("  {0} : sa propre reponse attendue est jugee '{1}'" -f $e.id, $v.verdict)
    }
  }
  catch { $malformees += ("  {0} : {1}" -f $e.id, $_.Exception.Message) }
}
if ($malformees.Count -gt 0) {
  throw ("jeu malforme, {0} epreuve(s), rien n'a ete demande a un modele :`n{1}" -f $malformees.Count, ($malformees -join "`n"))
}

# One failed request must never destroy a run. A full pass is 96 items of GPU
# time, and the first version of this bench died somewhere past item 20 on two
# consecutive runs, losing everything both times: $ErrorActionPreference is Stop
# at the top of this file and nothing caught the throw. Three retries, then the
# item is recorded as unanswered and the run carries on. An item nobody could
# ask is not a wrong answer, and it is reported on its own line.
function Demander($prompt, $maxTokens) {
  $corps = @{
    messages    = @(@{ role = 'user'; content = $prompt })
    max_tokens  = $maxTokens
    seed        = 42
    temperature = $Temperature
    top_k       = $TopK
    top_p       = $TopP
    chat_template_kwargs = @{ enable_thinking = [bool]$Reflexion }
  }
  # Zero est une VALEUR et non une absence : pour neutraliser une penalite posee
  # au lancement du serveur il faut l'envoyer explicitement, d'ou le defaut a -1
  # qui, lui, veut dire "ne rien envoyer et laisser le serveur decider".
  if ($PresencePenalty -ge 0) { $corps['presence_penalty'] = $PresencePenalty }
  $body = $corps | ConvertTo-Json -Depth 6 -Compress
  $octets = [Text.Encoding]::UTF8.GetBytes($body)

  for ($essai = 1; $essai -le 3; $essai++) {
    try {
      $r = Invoke-RestMethod -Uri $Uri -Method Post `
            -ContentType 'application/json; charset=utf-8' `
            -Body $octets -TimeoutSec 600 -ErrorAction Stop
      return @{ ok = $true; texte = $r.choices[0].message.content }
    } catch {
      $msg = $_.Exception.Message
      if ($essai -lt 3) {
        Write-Output ("  requete en echec (essai {0}/3) : {1}" -f $essai, $msg)
        Start-Sleep -Seconds (5 * $essai)
      } else {
        return @{ ok = $false; texte = $null; erreur = $msg }
      }
    }
  }
}

$t0 = Get-Date
$parFamille = @{}
$leurres = @()
$injoignables = @()
$fautives = @()
$n = 0

# Every raw answer is appended here as it arrives. Hours of GPU time must
# survive whatever kills the run, and a reply that was paid for once should
# never be paid for twice.
$brutDir = "$Dossier\bruts"
New-Item -ItemType Directory -Force -Path $brutDir | Out-Null
$journal = "$brutDir\$Label.jsonl"

# In replay the journal is the SOURCE, so it is read here and never truncated.
$rejoue = @{}
if ($Depouiller) {
  if (-not (Test-Path $journal)) { throw "rien a depouiller, $journal est absent" }
  foreach ($l in (Get-Content $journal -Encoding UTF8)) {
    if ($l.Trim()) { $o = ConvertFrom-Json $l; $rejoue[$o.id] = $o.brut }
  }
  Write-Output ("depouillement de {0} reponses deja enregistrees, aucun modele contacte" -f $rejoue.Count)
} else {
  Remove-Item $journal -ErrorAction SilentlyContinue
}

foreach ($e in $epreuves) {
  $n++
  # The wording carries its own output format instruction; nothing is appended
  # here, so what the writer validated is exactly what the model receives.
  $rep = if ($Depouiller) {
    if ($rejoue.ContainsKey($e.id)) { @{ ok = $true; texte = $rejoue[$e.id] } }
    else { @{ ok = $false; texte = $null; erreur = 'absente du journal rejoue' } }
  } else {
    Demander $e.q $e.max_tokens
  }

  if (-not $rep.ok) {
    $injoignables += ("  {0} : {1}" -f $e.id, $rep.erreur)
    $r = @{ verdict = 'injoignable'; lu = $null }
  } else {
    # One malformed item must never cost the whole run. The first campaign lost
    # two models at item 25 out of 96 because a correction fault was allowed to
    # propagate. A fault here is the item's, not the model's: it gets its own
    # verdict, it leaves the score alone, and it is named in the report.
    try {
      $r = Corriger $e $rep.texte
    } catch {
      $fautives += ("  {0} : {1}" -f $e.id, $_.Exception.Message)
      $r = @{ verdict = 'fautive'; lu = $null }
    }
  }

  if (-not $Depouiller) {
    @{ id = $e.id; family = $e.family; verdict = $r.verdict; lu = $r.lu; brut = $rep.texte } |
      ConvertTo-Json -Depth 4 -Compress | Add-Content -Path $journal -Encoding UTF8
  }

  if (-not $parFamille.ContainsKey($e.family)) {
    $parFamille[$e.family] = @{ total = 0; juste = 0; vide = 0; leurre = 0; injoignable = 0; fautive = 0 }
  }
  $parFamille[$e.family].total++
  switch ($r.verdict) {
    'juste'       { $parFamille[$e.family].juste++ }
    'vide'        { $parFamille[$e.family].vide++ }
    'injoignable' { $parFamille[$e.family].injoignable++ }
    'fautive'     { $parFamille[$e.family].fautive++ }
  }

  if ($r.verdict -ne 'juste' -and (EstLeurre $e $r.lu)) {
    $parFamille[$e.family].leurre++
    $leurres += ("  {0}  attendu {1}  leurre {2}  rendu {3}" -f $e.id, $e.answer, $e.lure, $r.lu)
  }

  if ($n % 10 -eq 0) { Write-Output ("{0}/{1} epreuves" -f $n, $epreuves.Count) }
}

$mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)

# Measure-Object -Property reads PROPERTIES, and the entries below are hash
# tables whose counters are KEYS. Windows PowerShell 5.1 throws rather than
# returning zero, which killed a run of 95 items after the last answer had been
# received and before a single figure was written. Summing by hand is shorter
# than the cmdlet call it replaces.
function Somme($tables, $cle) {
  $t = 0
  foreach ($x in $tables) { $t += $x[$cle] }
  return $t
}
$totJuste = Somme $parFamille.Values 'juste'
$totVide  = Somme $parFamille.Values 'vide'
$totLeurre= Somme $parFamille.Values 'leurre'
$totInj   = Somme $parFamille.Values 'injoignable'
$totFaut  = Somme $parFamille.Values 'fautive'
# The score divides by what was actually asked. Counting an unreachable item as
# a failure would blame the model for a network fault, and counting a faulty one
# would blame it for ours.
$poses = $epreuves.Count - $totInj - $totFaut
$pct = if ($poses -gt 0) { [math]::Round(100.0 * $totJuste / $poses, 1) } else { 0 }

$lignes = @(
  "=== $Label, jeu inedit",
  ("date        : {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm')),
  ("injoignables: {0} sur {1}" -f $totInj, $epreuves.Count),
  ("fautives    : {0} sur {1}, defaut du jeu, pas du modele" -f $totFaut, $epreuves.Count),
  ("vides       : {0} sur {1} posees" -f $totVide, $poses),
  ("score       : {0}/{1} soit {2} %" -f $totJuste, $poses, $pct),
  ("leurres     : {0}" -f $totLeurre),
  ''
)
foreach ($f in ($parFamille.Keys | Sort-Object)) {
  $s = $parFamille[$f]
  $lignes += ("  {0}  {1}/{2}  vides {3}  leurres {4}  injoignables {5}  fautives {6}" -f $f, $s.juste, $s.total, $s.vide, $s.leurre, $s.injoignable, $s.fautive)
}
if ($fautives.Count -gt 0) {
  $lignes += @('', 'EPREUVES FAUTIVES, a reparer avant la prochaine passe :') + $fautives
}
if ($injoignables.Count -gt 0) {
  $lignes += @('', 'INJOIGNABLES, trois essais chacune :') + $injoignables
}
if ($leurres.Count -gt 0) {
  $lignes += @('', 'REGURGITATION, item par item :') + $leurres
}
$lignes += @(
  '',
  ("duree       : {0} min" -f $mins),
  # La reflexion figure dans le protocole ecrit parce que deux passes qui en
  # different ne se comparent pas, et que rien d'autre dans le rapport ne le
  # dirait.
  ("protocole   : temperature {0}, top-k {1}, top-p {2}, graine 42, reflexion {3}" -f `
     $Temperature, $TopK, $TopP, $(if ($Reflexion) { 'ACTIVE' } else { 'desactivee' })),
  'lecture     : les vides se lisent AVANT le score. Un leurre est une reponse fausse',
  '              qui dit en plus pourquoi : le modele a recite au lieu de lire.'
)

$out = "$Dossier\resultats\banc-inedit-$Label.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
Set-Content -Path $out -Value $lignes -Encoding UTF8
$lignes | ForEach-Object { Write-Output $_ }
Write-Output "ecrit dans $out"
