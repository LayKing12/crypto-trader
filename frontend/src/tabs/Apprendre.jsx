import { useState } from "react";

const C = {
  green: "#00ff88", red: "#ff4d6d", yellow: "#f0b429", blue: "#00b4ff",
  text: "#e2e8f0", muted: "#4a5568", card: "rgba(255,255,255,0.03)",
  border: "rgba(255,255,255,0.06)",
};

const LESSONS = [
  {
    id: "rsi",
    icon: "📊",
    title: "RSI — Relative Strength Index",
    category: "Indicateurs",
    color: C.yellow,
    content: `Le RSI mesure la vitesse et l'ampleur des mouvements de prix. Il varie entre 0 et 100.

🔴 RSI > 70 : SURACHAT — le prix a monté trop vite. Signal de vente possible.
🟢 RSI < 30 : SURVENTE — le prix a chuté trop vite. Signal d'achat possible.
🟡 RSI 30-70 : Zone neutre, pas de signal clair.

💡 Exemple pratique:
Si BTC a un RSI de 78, cela signifie que beaucoup d'acheteurs ont déjà acheté. Il y a moins d'acheteurs potentiels restants → risque de correction à venir.

✅ Stratégie débutant: N'achetez jamais quand RSI > 75. Attendez qu'il redescende sous 65.`,
    quiz: {
      q: "Si le RSI de ETH est à 82, que devriez-vous faire ?",
      options: ["Acheter immédiatement", "Attendre / Réduire position", "Ignorer cet indicateur"],
      correct: 1,
      explain: "RSI 82 = zone de surachat extrême. Le risque de retournement est élevé. Mieux vaut attendre.",
    },
  },
  {
    id: "macd",
    icon: "📉",
    title: "MACD — Moving Average Convergence Divergence",
    category: "Indicateurs",
    color: C.blue,
    content: `Le MACD compare deux moyennes mobiles pour identifier la tendance et son momentum.

MACD > 0 : Tendance haussière (signal d'achat)
MACD < 0 : Tendance baissière (signal de vente)
Croisement MACD/Signal : Changement de tendance imminent

💡 Le MACD est meilleur pour confirmer une tendance que pour prédire un retournement.

✅ Stratégie: Utilisez le RSI pour trouver un point d'entrée, le MACD pour confirmer que la tendance est en votre faveur.`,
    quiz: {
      q: "MACD positif avec RSI à 45, quel signal ?",
      options: ["Signal baissier", "Signal haussier modéré", "Signal de vente urgent"],
      correct: 1,
      explain: "MACD > 0 = tendance haussière. RSI 45 = zone neutre, pas de surachat. Bon moment pour entrer prudemment.",
    },
  },
  {
    id: "bollinger",
    icon: "🎯",
    title: "Bandes de Bollinger",
    category: "Indicateurs",
    color: C.green,
    content: `Les bandes de Bollinger sont 3 lignes: une moyenne mobile + 2 bandes d'écart-type.

📈 Expansion haute : Prix proche de la bande supérieure → momentum haussier fort, mais attention au retournement
📉 Expansion basse : Prix proche de la bande inférieure → survente possible
〰️ Canal central : Marché en consolidation, pas de tendance forte

💡 "Squeeze" : Quand les bandes se resserrent = une forte explosion de prix est imminente (haussière ou baissière).

✅ Stratégie: Achetez près de la bande inférieure avec confirmation RSI < 35.`,
    quiz: {
      q: "Le prix de SOL touche la bande supérieure de Bollinger avec RSI 78. Que faire ?",
      options: ["Acheter — le momentum est fort", "Vendre ou ne rien faire — double signal de surachat", "Acheter la moitié"],
      correct: 1,
      explain: "Deux signaux de surachat simultanés = risque élevé. Mieux vaut ne rien faire ou prendre des profits.",
    },
  },
  {
    id: "dca",
    icon: "🔄",
    title: "DCA — Dollar Cost Averaging",
    category: "Stratégies",
    color: "#9945ff",
    content: `Le DCA consiste à investir un montant fixe à intervalles réguliers, peu importe le prix.

Exemple: Acheter 20€ de BTC chaque semaine.

✅ Avantages:
• Élimine le stress du "bon timing"
• Réduit l'impact de la volatilité
• Construit une position progressivement

❌ Inconvénients:
• Moins optimal si le prix monte toujours
• Requiert de la discipline sur le long terme

💡 Parfait pour les débutants: commencez avec 10-20€/semaine sur 1-2 cryptos.
Kraken permet les ordres récurrents automatiques.`,
    quiz: {
      q: "Avec le DCA, vous achetez 10€ de BTC par semaine. La semaine 1: BTC à 80k€, semaine 2: 70k€. Résultat ?",
      options: [
        "Vous avez perdu — BTC a baissé",
        "Votre prix moyen est meilleur qu'une grosse mise en semaine 1",
        "Indifférent — c'est pareil",
      ],
      correct: 1,
      explain: "Semaine 1: 10/80000 = 0.000125 BTC. Semaine 2: 10/70000 = 0.000143 BTC. Prix moyen: ~74k au lieu de 80k. Le DCA a réduit votre coût moyen !",
    },
  },
  {
    id: "risk",
    icon: "🛡️",
    title: "Gestion du risque — La règle des 1%",
    category: "Stratégies",
    color: C.red,
    content: `La règle fondamentale du trading: ne jamais risquer plus de 1-2% de votre capital sur un seul trade.

💡 Exemple:
Capital: 1000€ → Risque max par trade: 10-20€

Stop-loss: Placez toujours un niveau où vous sortez automatiquement.
Ex: Achat BTC à 80k → Stop-loss à 76k (-5%)

📐 Calcul de taille de position:
Risque € / (prix entrée - prix stop-loss) = volume à acheter

Exemple: 10€ de risque / (80000 - 76000) = 0.0025 BTC max à acheter

🚨 Règles d'or:
• Ne jamais investir plus que ce que vous pouvez perdre
• Jamais de levier en tant que débutant
• Diversifiez: max 50% sur une seule crypto`,
    quiz: {
      q: "Vous avez 500€. Combien risquer maximum par trade ?",
      options: ["100€ (20%)", "5-10€ (1-2%)", "250€ (50%)"],
      correct: 1,
      explain: "La règle des 1-2% protège votre capital. Avec 5-10€ de risque, vous pouvez faire 50 trades avant de perdre 50% du capital dans le pire cas.",
    },
  },
  {
    id: "fibonacci",
    icon: "🎯",
    title: "Fibonacci — Niveaux clés de support/résistance",
    category: "Stratégies",
    color: "#a78bfa",
    content: `Les niveaux de Fibonacci sont basés sur la suite mathématique de Fibonacci. Les traders les utilisent pour identifier les zones de support (plancher) et résistance (plafond) probables.

📐 Niveaux de retracement (zones d'achat sur correction):
• 23.6% — correction légère, tendance très forte
• 38.2% — correction modérée, tendance saine
• 50% — niveau psychologique important
• 61.8% ★ — "Golden Ratio" — zone d'achat la plus fiable
• 78.6% — correction profonde, tendance fragilisée

📈 Extensions (objectifs de profit):
• 127.2% — premier objectif conservateur (Vague 3 minimum)
• 161.8% ★ — "Golden Extension" — objectif Vague 3 idéal
• 261.8% — objectif ambitieux de long terme

💡 Comment utiliser:
1. Identifiez un mouvement récent (bas → haut)
2. Tracez les Fibonacci de ce mouvement
3. Lors de la prochaine correction, surveillez le niveau 61.8% comme zone d'achat
4. Placez votre stop-loss sous le niveau suivant (ex: sous 78.6%)
5. Objectif: niveau 161.8% = cible Vague 3 d'Elliott

✅ Cette app calcule automatiquement les niveaux Fibonacci dans l'onglet Marché → Vue Fibonacci.`,
    quiz: {
      q: "BTC a monté de 60k€ à 80k€. La correction s'arrête à 72.8k€. Quel niveau Fibonacci est-ce ?",
      options: ["23.6% (72.8k€)", "38.2% (72.36k€ → proche)", "61.8% (67.64k€)"],
      correct: 1,
      explain: "80000 - (80000-60000)×0.382 = 80000 - 7640 = 72360€ ≈ 72.8k. Le niveau 38.2% est une correction saine — signal d'achat modéré.",
    },
  },
  {
    id: "elliott",
    icon: "🌊",
    title: "Elliott Wave — Comprendre les cycles de marché",
    category: "Stratégies",
    color: "#00b4ff",
    content: `La théorie des Vagues d'Elliott décrit les marchés en cycles de 5 vagues haussières + 3 vagues correctives (A-B-C).

🌊 Les 5 vagues haussières:
• Vague 1: Premiers acheteurs — faible volume, peu connue
• Vague 2: Correction (jamais sous le début de Vague 1)
• Vague 3: ★ LA PLUS FORTE — 1.618× la Vague 1, fort volume, RSI monte
• Vague 4: Correction légère (ne dépasse pas le haut de Vague 1)
• Vague 5: Dernier push — divergence RSI souvent visible

📉 Les 3 vagues correctives (après le cycle haussier):
• Vague A: Première baisse — "c'est juste une correction"
• Vague B: Rebond trompeur — piège les acheteurs tardifs
• Vague C: Baisse finale — souvent la plus douloureuse

🎯 Comment trader avec Elliott:
• En Vague 2: ACHETER (correction avec Fibonacci 50-61.8%)
• En Vague 3: TENIR — c'est le mouvement le plus profitable
• En Vague 5: VENDRE progressivement (stratégie 10/20/20/40)
• En Vague A-B-C: Ne pas acheter "des bonnes affaires"

⚠️ Règle d'or: La Vague 3 n'est JAMAIS la plus courte des 3 vagues impulsives.

💡 Cible Vague 3 = Bas Vague 1 + (Haut Vague 1 - Bas Vague 1) × 1.618`,
    quiz: {
      q: "La Vague 1 va de 100€ à 150€. Où se trouve la cible idéale de la Vague 3 ?",
      options: ["180€ (1.618 × 50 = 80.9 + 100)", "130.9€", "180.9€ (100 + 50×1.618)"],
      correct: 2,
      explain: "Cible Vague 3 = début + (amplitude Vague 1) × 1.618 = 100 + 50 × 1.618 = 100 + 80.9 = 180.9€. La Vague 3 monte le plus fort et le plus longtemps.",
    },
  },
  {
    id: "exit",
    icon: "💰",
    title: "Stratégie de sortie 10/20/20/40",
    category: "Stratégies",
    color: "#f0b429",
    content: `La stratégie 10/20/20/40 vous permet de sécuriser des profits progressivement au lieu de tout vendre d'un coup (ou pire, de ne jamais vendre).

📊 Le plan de sortie:
• +10% de gain → Vendre 10% de la position (récupérer du capital)
• +20% de gain → Vendre 20% de la position (sécuriser les profits)
• +35% de gain → Vendre 20% de la position (cagnotter)
• +50%+ de gain → Vendre 40% restants (ou conserver comme "moonbag")

💡 Pourquoi cette stratégie?
• Vous ne pariez jamais "tout ou rien"
• Si le prix monte encore, vous gardez une position
• Si le prix redescend, vous avez déjà sécurisé une partie
• Évite le biais émotionnel ("je vais attendre encore un peu")

🎯 Exemple pratique:
Achat: 100€ de BTC à 80,000€
• À 88,000€ (+10%): vendre 10€ → récupéré €11
• À 96,000€ (+20%): vendre 20€ → récupéré €24.4
• À 108,000€ (+35%): vendre 20€ → récupéré €27
• À 120,000€ (+50%): vendre 40€ → récupéré €60
Total récupéré: ~€122.4 pour €100 investis = +22.4% net

+ 10% de chaque profit va automatiquement en cagnotte.

✅ Cette app calcule et affiche ces niveaux automatiquement dans l'onglet Trade.`,
    quiz: {
      q: "Vous achetez 200€ de SOL à 150€. SOL monte à 180€ (+20%). Que faites-vous ?",
      options: [
        "Vendre tout — le profit est bon",
        "Ne rien faire — ça peut monter encore",
        "Vendre 20% (40€) et garder le reste — stratégie 10/20/20/40",
      ],
      correct: 2,
      explain: "À +20%, la stratégie dit de vendre 20% = 40€ de votre position. Vous sécurisez €8 de profit (40€ → €48), et gardez 160€ exposés pour continuer à profiter si SOL monte encore.",
    },
  },
  {
    id: "kraken",
    icon: "⚙️",
    title: "Utiliser Kraken — Guide débutant",
    category: "Pratique",
    color: "#5c39e0",
    content: `Kraken est l'une des exchanges les plus fiables et réglementées.

🚀 Premiers pas:
1. Créez un compte sur kraken.com
2. Vérifiez votre identité (KYC) — obligatoire pour trader
3. Déposez des euros via virement SEPA (gratuit)
4. Créez des clés API avec les permissions limitées

🔑 Permissions API minimales pour cette app:
• Query Funds ✓
• Query Open Orders & Trades ✓
• Create & Modify Orders ✓
• Ne jamais cocher "Withdraw" !

💶 Montants minimaux sur Kraken:
• BTC: 0.0001 (~8€)
• ETH: 0.002 (~4€)
• SOL: 0.5 (~70€)
• ADA: 10 (~4€)

📊 Types d'ordres utiles:
• Market: exécuté immédiatement au meilleur prix
• Limit: exécuté uniquement à votre prix cible
→ Préférez les ordres limit pour maîtriser votre prix`,
    quiz: {
      q: "Pourquoi ne pas activer la permission 'Withdraw' sur vos clés API ?",
      options: [
        "Ce n'est pas important",
        "Si la clé est compromise, un hacker pourrait vider votre compte",
        "Kraken l'interdit",
      ],
      correct: 1,
      explain: "Les clés API sont des secrets. Si elles fuient, un attaquant avec la permission 'Withdraw' peut transférer vos fonds. Limitez toujours les permissions au strict nécessaire.",
    },
  },
];

function LessonCard({ lesson }) {
  const [open, setOpen] = useState(false);
  const [quizAnswer, setQuizAnswer] = useState(null);

  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: "12px", marginBottom: "10px", overflow: "hidden" }}>
      <button onClick={() => setOpen(!open)} style={{
        width: "100%", padding: "16px", display: "flex", alignItems: "center", gap: "12px",
        background: "transparent", border: "none", color: C.text, cursor: "pointer",
        fontFamily: "inherit", textAlign: "left",
      }}>
        <span style={{ fontSize: "22px" }}>{lesson.icon}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: "13px", fontWeight: "600" }}>{lesson.title}</div>
          <div style={{ fontSize: "10px", color: lesson.color, letterSpacing: "1px", marginTop: "2px" }}>
            {lesson.category}
          </div>
        </div>
        <span style={{ color: C.muted, fontSize: "16px", transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>▼</span>
      </button>

      {open && (
        <div style={{ padding: "0 16px 16px" }}>
          <div style={{ width: "100%", height: "1px", background: C.border, marginBottom: "14px" }} />
          <div style={{ fontSize: "13px", color: "#94a3b8", lineHeight: "1.8", whiteSpace: "pre-wrap", marginBottom: "16px" }}>
            {lesson.content}
          </div>

          {/* Quiz */}
          <div style={{ background: "rgba(255,255,255,0.02)", border: `1px solid ${lesson.color}30`, borderRadius: "10px", padding: "14px" }}>
            <div style={{ fontSize: "10px", color: lesson.color, letterSpacing: "1px", marginBottom: "10px" }}>
              🎓 QUIZ
            </div>
            <div style={{ fontSize: "13px", color: C.text, marginBottom: "12px", lineHeight: "1.5" }}>
              {lesson.quiz.q}
            </div>
            {lesson.quiz.options.map((opt, i) => {
              const answered = quizAnswer !== null;
              const isCorrect = i === lesson.quiz.correct;
              const isSelected = i === quizAnswer;
              let bg = "rgba(255,255,255,0.04)";
              let border = C.border;
              let color = C.muted;
              if (answered) {
                if (isCorrect) { bg = "rgba(0,255,136,0.12)"; border = C.green + "60"; color = C.green; }
                else if (isSelected) { bg = "rgba(255,77,109,0.12)"; border = C.red + "60"; color = C.red; }
              }
              return (
                <button key={i} onClick={() => !answered && setQuizAnswer(i)} style={{
                  display: "block", width: "100%", padding: "10px 12px", marginBottom: "6px",
                  borderRadius: "8px", background: bg, border: `1px solid ${border}`,
                  color, fontFamily: "inherit", fontSize: "12px", cursor: answered ? "default" : "pointer",
                  textAlign: "left", transition: "all 0.2s",
                }}>
                  {answered && isCorrect ? "✓ " : answered && isSelected ? "✗ " : ""}{opt}
                </button>
              );
            })}
            {quizAnswer !== null && (
              <div style={{ fontSize: "11px", color: quizAnswer === lesson.quiz.correct ? C.green : "#94a3b8", marginTop: "8px", lineHeight: "1.5" }}>
                💡 {lesson.quiz.explain}
              </div>
            )}
            {quizAnswer !== null && (
              <button onClick={() => setQuizAnswer(null)} style={{
                marginTop: "8px", padding: "6px 14px", borderRadius: "6px",
                background: "transparent", border: `1px solid ${C.border}`,
                color: C.muted, fontFamily: "inherit", fontSize: "11px", cursor: "pointer",
              }}>Réessayer</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Apprendre() {
  const categories = [...new Set(LESSONS.map((l) => l.category))];
  const [filter, setFilter] = useState("Tous");

  const filtered = filter === "Tous" ? LESSONS : LESSONS.filter((l) => l.category === filter);

  return (
    <div style={{ padding: "16px", paddingBottom: "80px" }}>
      <div style={{ fontSize: "10px", color: C.muted, letterSpacing: "1px", marginBottom: "14px" }}>
        CENTRE D'APPRENTISSAGE
      </div>

      {/* Category filter */}
      <div style={{ display: "flex", gap: "8px", overflowX: "auto", paddingBottom: "4px", marginBottom: "14px" }}>
        {["Tous", ...categories].map((cat) => (
          <button key={cat} onClick={() => setFilter(cat)} style={{
            flexShrink: 0, padding: "6px 14px", borderRadius: "20px",
            border: `1px solid ${filter === cat ? C.green : C.border}`,
            background: filter === cat ? "rgba(0,255,136,0.1)" : C.card,
            color: filter === cat ? C.green : C.muted,
            fontFamily: "inherit", fontSize: "11px", cursor: "pointer",
          }}>{cat}</button>
        ))}
      </div>

      {filtered.map((lesson) => (
        <LessonCard key={lesson.id} lesson={lesson} />
      ))}

      <div style={{ textAlign: "center", padding: "20px", fontSize: "11px", color: C.muted, lineHeight: "1.7" }}>
        🎓 Maîtrisez les bases avant de trader avec de vraies sommes.<br />
        Commencez avec les montants minimum de Kraken (≈ 5-10€).
      </div>
    </div>
  );
}
