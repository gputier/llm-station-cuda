param([Parameter(Mandatory=$true)][string]$Label)

# Four checks at temperature 0, written to a file so the answers can be read
# side by side. Throughput says nothing about whether a model is usable.
$questions = @(
  @{ nom = 'bug-cpp'; texte = @'
Voici une fonction C++. Elle contient exactement un defaut qui provoque un comportement indefini.
Nomme-le en une phrase, puis donne la ligne corrigee. Ne reecris pas toute la fonction.

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
    std::string out;
    for (size_t i = 0; i <= parts.size(); ++i) {
        out += parts[i];
        if (i + 1 < parts.size()) out += sep;
    }
    return out;
}
'@ },
  @{ nom = 'ecriture-code'; texte = @'
Ecris une fonction PowerShell Get-LargestFiles qui prend un chemin et un entier N et renvoie les N
plus gros fichiers sous ce chemin, recursivement, avec leur taille en mega-octets arrondie a deux
decimales. Elle doit ignorer les erreurs d acces sans les afficher. Code seul, aucun commentaire,
aucune explication avant ou apres.
'@ },
  @{ nom = 'raisonnement'; texte = @'
Deux trains partent l un vers l autre a 8h00, distants de 300 km. Le premier roule a 90 km/h, le
second a 60 km/h. Une mouche part du premier train a 8h00, vole a 120 km/h vers le second, fait
demi-tour des qu elle le touche, et ainsi de suite jusqu a ce que les trains se croisent. Quelle
distance totale la mouche parcourt-elle ? Reponds en francais, en trois lignes au plus.
'@ },
  @{ nom = 'lecture-de-code'; texte = @'
Que renvoie cet appel Python, et pourquoi ? Reponds en deux lignes.

def f(a, acc=[]):
    acc.append(a)
    return acc

f(1); f(2); print(f(3))
'@ }
)

$out = "D:\LLM-Setup\bench\resultats\quality-$Label.txt"
Set-Content -Path $out -Value "=== $Label" -Encoding UTF8
foreach ($q in $questions) {
  $body = @{
    messages = @(@{ role = 'user'; content = $q.texte })
    max_tokens = 700
    seed = 42
    temperature = 0
    chat_template_kwargs = @{ enable_thinking = $false }
  } | ConvertTo-Json -Depth 6 -Compress
  $t0 = Get-Date
  $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/chat/completions' -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 600
  $s = [int]((Get-Date) - $t0).TotalSeconds
  Add-Content -Path $out -Encoding UTF8 -Value ("`n--- {0} ({1} s, {2:N1} tok/s)`n{3}" -f $q.nom, $s, $r.timings.predicted_per_second, $r.choices[0].message.content)
  Write-Output ("{0} : {1} s" -f $q.nom, $s)
}
Write-Output "ecrit dans $out"
