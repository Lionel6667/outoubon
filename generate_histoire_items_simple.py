"""
Script pour générer des dissertations et questions texte supplémentaires.
Utilise l'API Gemini directement sans Django.
"""
import json
from pathlib import Path
import os

# Données de génération pré-définies pour éviter les appels API
DISSERTATIONS_GENERATED = [
    {
        "type": "dissertation",
        "theme": "Révolution française et ses conséquences",
        "enonce": "Analysez l'impact de la Révolution française sur les mouvements d'indépendance en Amérique latine.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Colonialisme en Afrique",
        "enonce": "Expliquez les causes et les conséquences de la colonisation de l'Afrique par les puissances européennes.",
        "difficulte": "difficile",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Guerre froide",
        "enonce": "Décrivez les principaux conflits de la Guerre froide et leur impact sur la géopolitique mondiale.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Révolution haïtienne",
        "enonce": "Montrez comment la Révolution haïtienne de 1791 a transformé la société coloniale et influencé le monde.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Indépendance latino-américaine",
        "enonce": "Analysez les facteurs politiques et économiques qui ont motivé les guerres d'indépendance en Amérique latine.",
        "difficulte": "difficile",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "La Mondialisation",
        "enonce": "Évaluez les impacts positifs et négatifs de la mondialisation sur les économies des pays développés et en développement.",
        "difficulte": "difficile",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Mouvements d'émancipation",
        "enonce": "Comparez les stratégies des mouvements d'émancipation du 20ème siècle en Afrique et en Asie.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Géopolitique du Moyen-Orient",
        "enonce": "Analysez les enjeux géopolitiques et économiques qui structurent les conflits au Moyen-Orient depuis 1945.",
        "difficulte": "difficile",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Intégration régionale",
        "enonce": "Examinez les objectifs et les défis des processus d'intégration régionale comme l'UE et l'ALENA.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Crises économiques",
        "enonce": "Décrivez les causes et les conséquences des grandes crises économiques du 20ème siècle.",
        "difficulte": "difficile",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Fin de l'URSS",
        "enonce": "Expliquez les facteurs qui ont conduit à l'effondrement de l'Union soviétique en 1991.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Décolonisation en Asie",
        "enonce": "Analysez les processus de décolonisation en Asie du Sud et du Sud-Est après 1945.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Totalitarisme du 20ème siècle",
        "enonce": "Comparez les caractéristiques du totalitarisme nazi, fasciste et stalinien.",
        "difficulte": "difficile",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Organisations internationales",
        "enonce": "Évaluez le rôle des Nations Unies dans la résolution des conflits internationaux depuis 1945.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
    {
        "type": "dissertation",
        "theme": "Développement durable",
        "enonce": "Discutez des enjeux du développement durable et des moyens de concilier croissance économique et protection environnementale.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated"
    },
]

QUESTIONS_TEXTE_GENERATED = [
    {
        "type": "question_texte",
        "theme": "Traité de Versailles",
        "enonce": "Présentez le document et identifiez ses principales clauses concernant l'Allemagne.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "Le Traité de Versailles, signé le 28 juin 1919, a marqué la fin officielle de la Première Guerre mondiale. Ce traité imposait des conditions sévères à l'Allemagne vaincue. Parmi ses dispositions les plus importantes figuraient la perte de 13% du territoire européen allemand, le paiement de réparations de 132 milliards de marks-or, le désarmement militaire limité à 100 000 hommes, et la responsabilité totale de la guerre attribuée à l'Allemagne. Ces conditions humiliantes ont créé des ressentiments durables qui ont contribué à l'instabilité politique allemande dans les années 1920 et à la montée du nazisme."
    },
    {
        "type": "question_texte",
        "theme": "Plan Marshall",
        "enonce": "Relevez les objectifs économiques et politiques du Plan Marshall après 1948.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "Le Plan Marshall, lancé en 1948, était un programme d'aide économique américaine destiné à reconstruire l'Europe occidentale après les destructions de la Seconde Guerre mondiale. Les États-Unis ont investi environ 13 milliards de dollars (équivalent de plus de 130 milliards aujourd'hui) pour restaurer les infrastructures, les industries et les transports européens. Au-delà de son objectif économique évident, le Plan Marshall avait aussi une dimension géopolitique : en renforçant l'économie européenne, il visait à contrer l'influence soviétique et à stabiliser les régimes démocratiques face à la menace communiste."
    },
    {
        "type": "question_texte",
        "theme": "Révolution cubaine",
        "enonce": "Expliquez les causes de la Révolution cubaine et ses conséquences pour la région.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "La Révolution cubaine de 1959, menée par Fidel Castro et Che Guevara, a renversé la dictature de Batista. Les causes principales inclaient la pauvreté, l'inégalité sociale, la domination économique américaine et la corruption du régime en place. Après la prise du pouvoir, Cuba s'est rapprochée de l'Union soviétique et a adopté un système socialiste. Cette transformation a créé une tension majeure lors de la crise des missiles de 1962 et a influence les mouvements révolutionnaires en Amérique latine pendant plusieurs décennies."
    },
    {
        "type": "question_texte",
        "theme": "Décolonisation de l'Afrique",
        "enonce": "Dégagez les différentes étapes et acteurs de la décolonisation africaine.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "La décolonisation de l'Afrique s'est déroulée en plusieurs phases. Dans les années 1950-60, les colonies britanniques et françaises ont accédé progressivement à l'indépendance. L'Algérie, colonie de peuplement français, n'a obtenu son indépendance qu'après une longue guerre (1954-1962). Les mouvements d'indépendance étaient menés par des élites éduquées et des leaders charismatiques comme Kwame Nkrumah au Ghana, Julius Nyerere en Tanzanie et Nelson Mandela en Afrique du Sud. La fin de la colonisation a transformé la carte politique de l'Afrique et a créé de nouveaux défis liés aux frontières héritées et aux questions d'identité nationale."
    },
    {
        "type": "question_texte",
        "theme": "Chute du mur de Berlin",
        "enonce": "Analysez les événements qui ont conduit à la chute du mur de Berlin en 1989.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "La chute du mur de Berlin le 9 novembre 1989 symbolisait l'effondrement de la division Est-Ouest en Europe. Cette chute était le résultat d'une combinaison de facteurs : les réformes de Mikhail Gorbatchev en URSS (glasnost et perestroïka), les mouvements de démocratisation en Pologne et en Hongrie, et la prise de conscience générale que le système soviétique était en crise économique et politique. L'ouverture des frontières hongroises en septembre 1989 avait déjà permis à de nombreux Allemands de l'Est de s'échapper. La chute du mur a marqué le début de la réunification allemande et la fin effective de la Guerre froide."
    },
    {
        "type": "question_texte",
        "theme": "Indépendance des États-Unis",
        "enonce": "Présentez les principes énoncés dans la Déclaration d'indépendance américaine.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "La Déclaration d'indépendance des États-Unis, signée le 4 juillet 1776, affirmait les principes fondamentaux de la révolution américaine. Elle déclarait que 'tous les hommes sont créés égaux' et possèdent des 'droits inaliénables' incluant la vie, la liberté et la quête du bonheur. Elle affirmait que le pouvoir gouvernemental dérivait du consentement des gouvernés et que les peuples avaient le droit de renverser les gouvernements oppressifs. Ces principes, énoncés par Thomas Jefferson, ont influencé les penseurs révolutionnaires en France et ont jeté les bases de la démocratie moderne."
    },
    {
        "type": "question_texte",
        "theme": "Traité de Maastricht",
        "enonce": "Expliquez les innovations apportées par le Traité de Maastricht à la construction européenne.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "Le Traité de Maastricht, signé en 1992, a transformé la Communauté économique européenne en Union européenne. Il a introduit plusieurs innovations majeures : la citoyenneté européenne, permettant aux citoyens de circuler librement; le projet d'une monnaie unique (l'euro, introduit en 1999); la politique étrangère et de sécurité commune; la politique intérieure et judiciaire. Le traité a aussi établi le système de vote pondéré au Conseil et a renforcé le rôle du Parlement européen. Ces changements ont marqué un pas important vers une intégration politique plus profonde de l'Europe."
    },
    {
        "type": "question_texte",
        "theme": "Apartheid en Afrique du Sud",
        "enonce": "Relevez les caractéristiques principales du système d'apartheid et sa fin.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "L'apartheid était un système de ségrégation raciale légale en Afrique du Sud de 1948 à 1994. Ce système classait la population en quatre groupes raciaux (blancs, noirs, métis, asiatiques) et attribuait des droits et des privileges différents selon la race. Les noirs étaient exclus de la vie politique et économique et confinés à des townships ségrégués. Nelson Mandela, leader du Congrès national africain (ANC), a mené la lutte contre l'apartheid pendant 27 ans de prison. La fin de l'apartheid a commencé avec les réformes de Frederik Willem de Klerk au début des années 1990 et s'est conclue avec l'élection démocratique de 1994 qui a porté Mandela à la présidence."
    },
    {
        "type": "question_texte",
        "theme": "Conférence de Potsdam",
        "enonce": "Dégagez les décisions principales prises à la Conférence de Potsdam en 1945.",
        "difficulte": "difficile",
        "source": "Bac synthétique - generated",
        "texte": "La Conférence de Potsdam (juillet-août 1945) réunissait les trois grands vainqueurs de la Seconde Guerre mondiale: les États-Unis (Truman), l'URSS (Staline) et le Royaume-Uni (Churchill, puis Attlee). Cette conférence a pris des décisions cruciales pour l'après-guerre: le déplacement vers l'ouest des frontières de la Pologne, l'expulsion de millions d'Allemands vers l'ouest, la division de l'Allemagne en quatre zones d'occupation, et le départ des Alliés soviétiques en Mandchourie pour attaquer le Japon. Ces décisions ont jeté les bases de la Guerre froide et de la transformation géopolitique de l'Europe."
    },
    {
        "type": "question_texte",
        "theme": "Émeutes de Stonewall",
        "enonce": "Présentez les événements des émeutes de Stonewall et leur impact sur le mouvement LGBTQ+.",
        "difficulte": "moyen",
        "source": "Bac synthétique - generated",
        "texte": "Les émeutes de Stonewall survenues en juin 1969 à New York marquent un point tournant dans l'histoire du mouvement LGBTQ+. Lors d'un raid du bar Stonewall Inn, la police a arrêté des personnes LGBTQ+. Contrairement aux arrestations précédentes acceptées passivement, les clients ont vivement résisté, déclenchant plusieurs nuits d'affrontements. Ces émeutes ont provoqué une prise de conscience collective et ont inspiré l'organisation de marches pour les droits gays. Bien que la discrimination persiste, les émeutes de Stonewall marquent le début du mouvement de libération LGBTQ+ moderne et du combat pour les droits civiques basés sur l'orientation sexuelle."
    },
]

def load_histoire_json():
    """Charge le fichier exams_histoire.json."""
    path = Path('database/json/exams_histoire.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_histoire_json(data):
    """Sauvegarde le fichier exams_histoire.json."""
    path = Path('database/json/exams_histoire.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    print("🔄 Chargement de exams_histoire.json...")
    try:
        history_data = load_histoire_json()
    except FileNotFoundError:
        print("❌ Fichier non trouvé!")
        return
    
    print(f"   ✅ {len(history_data['exams'])} exams chargés")
    
    # Ajouter à la dernière catégorie d'exams
    if history_data['exams']:
        last_exam = history_data['exams'][-1]
        if 'items' not in last_exam:
            last_exam['items'] = []
        
        print(f"\n📝 Ajout de {len(DISSERTATIONS_GENERATED)} dissertations...")
        last_exam['items'].extend(DISSERTATIONS_GENERATED)
        
        print(f"📝 Ajout de {len(QUESTIONS_TEXTE_GENERATED)} questions texte...")
        last_exam['items'].extend(QUESTIONS_TEXTE_GENERATED)
        
        print(f"\n💾 Total items avant: inconnu")
        print(f"   Total items après: {len(last_exam['items'])}")
    
    print("\n💾 Sauvegarde de exams_histoire.json...")
    save_histoire_json(history_data)
    
    print("\n✅ Génération complétée!")
    print(f"   ✨ Base agrandie: {len(DISSERTATIONS_GENERATED)} dissertations + {len(QUESTIONS_TEXTE_GENERATED)} questions texte")
    print(f"   📊 Total nouveaux items: {len(DISSERTATIONS_GENERATED) + len(QUESTIONS_TEXTE_GENERATED)}")

if __name__ == '__main__':
    main()
