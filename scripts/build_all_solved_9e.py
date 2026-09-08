"""Build all_solved.json from clean MCQs + verified answers."""
from __future__ import annotations

import json
import re
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "database"
CLEAN = DB / "_mcq_clean_9e.json"
OUT = DB / "_gemini_responses" / "all_solved.json"

# question prefix -> (correct letter, explanation, category, difficulty)
ANSWERS: dict[str, tuple[str, str, str, str]] = {
    # art
    "Une ligne est créée par": (
        "B",
        "En géométrie et en arts plastiques, une ligne est le trace laissé par le déplacement d'un point dans l'espace. Une gamme, une feuille ou une règle sont des outils, pas la définition même de la ligne.",
        "Arts plastiques",
        "facile",
    ),
    "Le dessin technique est souvent utilisé en": (
        "B",
        "Le dessin technique sert à représenter avec précision des plans, des coupes et des mesures. Il est indispensable en architecture, en ingénierie et dans l'industrie pour construire des ouvrages.",
        "Arts plastiques",
        "facile",
    ),
    # histoire
    "Dans l'une des villes suivantes à eu lieu un krach boursier": (
        "C",
        "La crise économique de 1929 a commencé avec l'effondrement de la Bourse de New York le 24 octobre 1929 (Jeudi noir). Cet événement a déclenché une récession mondiale.",
        "Histoire mondiale",
        "facile",
    ),
    "L'un des personnages suivants fut le secrétaire particulier de Jean Jacques Dessalines": (
        "B",
        "Boisrond-Tonnerre fut le secrétaire particulier de Jean-Jacques Dessalines et rédigea la proclamation de l'indépendance d'Haïti le 1er janvier 1804.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Tirésias Simon Sam a succédé à l'un des chefs d'État suivants": (
        "C",
        "Tirésias Simon Sam devint président d'Haïti en 1896 après la mort du président Nord Alexis, qu'il avait servi comme ministre.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "L'un des pays membres de la CARICOM est situé en Amérique centrale": (
        "C",
        "Belize, membre de la CARICOM, se situe en Amérique centrale, sur la côte caraïbe. Les autres options sont des îles des Antilles.",
        "Géographie",
        "facile",
    ),
    "Tous les éléments ci-dessous sont des causes du sous-développement d'Haïti, sauf un": (
        "D",
        "L'entraide sociale est une ressource communautaire qui peut aider le développement. L'instabilité politique, la corruption et l'insécurité sociale sont au contraire des freins reconnus au développement.",
        "Économie",
        "moyen",
    ),
    "L'une des religions suivantes a pour prophète Mahomet": (
        "B",
        "L'islam reconnaît Mahomet comme dernier prophète et le Coran comme livre sacré. Le catholicisme, le judaïsme et le bouddhisme ont d'autres fondateurs et textes fondateurs.",
        "Culture générale",
        "facile",
    ),
    "Au cours de la Première Guerre Mondiale, le Traité de Versailles avait lié": (
        "B",
        "Le Traité de Versailles de 1919 a surtout lié l'Allemagne aux puissances alliées vainqueures. L'Allemagne faisait partie de la coalition des Empires centraux avec l'Autriche-Hongrie.",
        "Histoire mondiale",
        "moyen",
    ),
    "Le secteur tertiaire en Haïti correspond à l'un des": (
        "B",
        "Le secteur tertiaire regroupe les services : banques, écoles, cabinets d'avocats, commerce et administration. Il ne comprend ni l'agriculture ni l'industrie lourde.",
        "Économie",
        "moyen",
    ),
    "Au nom des Piquets, Jean Jacques Acaau a formulé": (
        "C",
        "Le mouvement des Piquets, mené par Jean-Jacques Acaau, revendiquait notamment qu'un Noir accède à la présidence pour corriger les inégalités après l'indépendance.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Lequel de ces événements a contribueé à mettre fin": (
        "B",
        "Le bombardement atomique d'Hiroshima le 6 août 1945 a accéléré la capitulation du Japon et marqué la fin de la Seconde Guerre mondiale en Asie.",
        "Histoire mondiale",
        "facile",
    ),
    "L'une des affirmations suivantes correspond l'objectif de la révolution bolchévique": (
        "C",
        "La révolution d'Octobre 1917 visait à renverser le régime tsariste et à instaurer un pouvoir socialiste en Russie, fondé sur les soviets ouvriers et paysans.",
        "Histoire mondiale",
        "moyen",
    ),
    "Tous les faits suivants relèvent de l'occupation américaine d'Haiti de 1915 à 1934, saufun": (
        "D",
        "Dumarsais Estimé a été élu président en 1946, soit après le retrait des Marines américains en 1934. Les autres faits appartiennent bien à la période d'occupation.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Tous les faits énumérés ci-dessous sont vrais, sauf un": (
        "B",
        "Benito Mussolini a fondé le fascisme italien, mais ce n'est pas lui qui a créé le parti nazi. Adolf Hitler est le fondateur du NSDAP (parti nazi) en Allemagne.",
        "Histoire mondiale",
        "moyen",
    ),
    "Toutes les actions suivantes peuvent faire partie d'un programme d'aménagement du territoir": (
        "D",
        "L'utilisation du bois comme combustible accentue la déforestation et va à l'encontre de l'aménagement durable. Protéger les forêts, ramasser les ordures et recycler sont au contraire des actions d'aménagement.",
        "Géographie",
        "moyen",
    ),
    "Toutes les définitions suivantes sont liées à l'agriculture commerciale, sauf une": (
        "A",
        "L'agriculture de subsistance vise la consommation familiale locale, pas la vente ni l'exportation. L'agriculture commerciale est spécialisée, mécanisée et orientée vers le marché.",
        "Économie",
        "moyen",
    ),
    "Lequel des commissaires civils français suivants fut à la base de l'affranchissement": (
        "A",
        "Léger-Félicité Sonthonax proclama l'abolition de l'esclavage à Saint-Domingue en 1793, avant l'abolition générale de 1794, ce qui en fit une figure centrale de l'affranchissement.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "L'un des groupes de personnages suivants dest constitué uniquement de fondateurs de la République d'Argentine": (
        "B",
        "José de San Martín et Manuel Belgrano sont deux héros fondateurs de l'indépendance argentine. Bolívar et O'Higgins sont plutôt liés à d'autres pays sud-américains.",
        "Histoire latino-américaine",
        "moyen",
    ),
    "Lequel des groupes de pays suivants constitua les forces de l": (
        "C",
        "Les forces de l'Axe durant la Seconde Guerre mondiale étaient l'Allemagne, l'Italie et le Japon. La France, l'Angleterre et les États-Unis appartenaient au camp allié.",
        "Histoire mondiale",
        "facile",
    ),
    "Toutes les conséquences suivantes découlent de la crise économique de 1929, sauf une": (
        "C",
        "La crise de 1929 a provoqué faillites bancaires et chômage massif, pas un développement bancaire. L'inflation, l'arrêt de production et la misère en furent au contraire des conséquences directes.",
        "Histoire mondiale",
        "moyen",
    ),
    "Lors de la bataille de Vertières le 18 novembre 1803": (
        "C",
        "Lors de la bataille de Vertières, le général François Capois, dit Capois-la-Mort, continua l'assaut malgré les tirs français et fut salué par Rochambeau pour sa bravoure.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Parmi les évènements suivants lequel est connu dans l'histoire d'Haïti sous l'appellation": (
        "A",
        "Le « drame du Pont-Rouge » désigne l'assassinat de Jean-Jacques Dessalines le 17 octobre 1806 près du Pont-Rouge, au sud de Port-au-Prince.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Tous les facteurs suivants caractérisent les pays sous- développés, sauf, lequel": (
        "B",
        "Les pays sous-développés présentent généralement un chômage élevé, pas faible. L'analphabétisme, l'agriculture vivrière et le déficit commercial sont des traits typiques du sous-développement.",
        "Économie",
        "moyen",
    ),
    "L'une des chaines de montagnes suivantes se trouvent dans les limites des départements du Sud": (
        "C",
        "Le massif de la Hotte s'étend dans le Sud, la Grand'Anse et les Nippes. Les montagnes Noires et le massif de Terre-Neuve se situent plutôt dans d'autres régions du pays.",
        "Géographie d'Haïti",
        "difficile",
    ),
    "Lequel des généraux suivants commandait l'armée expéditionnaire française lors de la bataille de Vertières": (
        "B",
        "Donatien-Marie-Joseph de Vimeur, vicomte de Rochambeau, commandait les forces françaises lors de la bataille de Vertières en novembre 1803.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Le conseil municipal relève de l'un desa ministè": (
        "C",
        "En Haïti, les collectivités territoriales et les conseils municipaux relèvent du ministère de l'Intérieur et des Collectivités territoriales, qui supervise l'administration locale.",
        "Institutions",
        "moyen",
    ),
    "Toutes les caractéristiues suivantes correspondent Porto- rico, sauf une": (
        "A",
        "Porto Rico n'est pas producteur de pétrole. C'est le plus petit des grands États des Antilles et un territoire associé aux États-Unis depuis 1952.",
        "Géographie",
        "moyen",
    ),
    "Lequel des événements suivants s'est déroulé pendant la Seconde Guerre Mondiale": (
        "C",
        "Le génocide des Juifs (Shoah) s'est déroulé pendant la Seconde Guerre mondiale. La révolution russe date de 1917, le krach de 1929 est antérieur, et la décolonisation africaine s'accélère surtout après 1960.",
        "Histoire mondiale",
        "facile",
    ),
    "L'un des chefs d'état haïtiens a maté la révolte de ! Goman": (
        "A",
        "Jean-Pierre Boyer, président d'Haïti, a écrasé la révolte de Goman dans la Grand'Anse au début des années 1820, consolidant ainsi l'autorité de l'État.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "La faim peut être provoquée par l'un des facteurs suivants": (
        "B",
        "La faim résulte souvent d'une production agricole insuffisante ou mal répartie. Le reboisement, l'utilisation de machines et l'augmentation des surfaces cultivables tendent au contraire à améliorer la sécurité alimentaire.",
        "Économie",
        "facile",
    ),
    "L'une des conséquences politiques de l'assassinat de Dessalines, le 17 octobre 1806 est": (
        "A",
        "Après l'assassinat de Dessalines en 1806, le pays s'est scindé en deux États : le royaume d'Henri Christophe au nord et la république d'Alexandre Pétion au sud.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "L'un de ces groupes comporte uniquement des denrées cultivées dans les montagnes": (
        "C",
        "En Haïti, le café, le cacao et les produits maraîchers sont typiquement cultivés en zones montagneuses ou sur terrasses. La canne à sucre se cultive surtout en plaine.",
        "Géographie d'Haïti",
        "moyen",
    ),
    "Tous les indicateurs suivants caractérisent les pays du Tiers-monde, sauf un": (
        "C",
        "Les pays du Tiers-monde ont un PIB par habitant faible, pas élevé. L'analphabétisme, la faible espérance de vie et les infrastructures sanitaires insuffisantes sont des indicateurs classiques.",
        "Économie",
        "facile",
    ),
    "Le processus de l'effondrement du système colonial et esclavagiste à St Domingue est marqué": (
        "D",
        "Le retour de Rigaud en 1810 intervient après l'indépendance, lors de la guerre civile entre Pétion et Christophe. Il ne fait pas partie du processus d'effondrement du système colonial esclavagiste.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "La date du 8 mai 1945 rappelle l'un des faits historiques suivants": (
        "D",
        "Le 8 mai 1945 marque la capitulation de l'Allemagne nazie et la fin de la guerre en Europe (VE Day). Hiroshima et Nagasaki ont été bombardés en août 1945.",
        "Histoire mondiale",
        "facile",
    ),
    "Lequel de ces groupes de pays fait partie des Antilles de langue française": (
        "C",
        "Haïti, la Martinique et la Guadeloupe sont des territoires de langue française dans les Antilles. Porto Rico est hispanophone, Cuba et la Jamaïque ne sont pas francophones.",
        "Géographie",
        "facile",
    ),
    "Le 09 août 1945 rappelle l'un des faits historiques suivants": (
        "D",
        "Le 9 août 1945, les États-Unis ont largué une bombe atomique sur Nagasaki. Hiroshima avait été bombardée le 6 août 1945.",
        "Histoire mondiale",
        "facile",
    ),
    "Lequel de ces groupes de pays constitue uniquement des dragons de l'Asie du Sud'Est": (
        "C",
        "Les « dragons asiatiques » désignent Hong Kong, Taïwan, Singapour et la Corée du Sud, économies à forte croissance. L'Iran, la Turquie et l'Irak n'appartiennent pas à ce groupe.",
        "Économie",
        "difficile",
    ),
    "Lequel des groupes de pays suivants fait partie du CARICOM": (
        "B",
        "Les Bahamas, Belize et Haïti sont membres de la CARICOM. La Martinique est française, Cuba n'en fait pas partie, et Aruba relève du Royaume des Pays-Bas.",
        "Géographie",
        "moyen",
    ),
    "L'un des événements majeurs suivants a marqué le monde à partir de 1960": (
        "B",
        "À partir des années 1960, la décolonisation de l'Afrique a transformé la carte politique mondiale avec l'indépendance de dizaines de pays africains.",
        "Histoire mondiale",
        "facile",
    ),
    "Lequel des groupes de pays suivants constituait la Triple ! Alliance": (
        "C",
        "La Triple Alliance avant 1914 réunissait l'Allemagne, l'Autriche-Hongrie et l'Italie. La France, l'Angleterre et la Russie formaient la Triple Entente.",
        "Histoire mondiale",
        "moyen",
    ),
    "Parmi les groupes de pays suivants, lequel est formé [e uniquement des pays de la Triple Entente": (
        "D",
        "La Triple Entente regroupait la Russie, la France et l'Angleterre (Royaume-Uni). L'Allemagne, l'Autriche-Hongrie et l'Italie appartenaient au camp adverse.",
        "Histoire mondiale",
        "moyen",
    ),
    "La date du 13 mars 1843 se réfère à l'un des événements suivants": (
        "B",
        "Le 13 mars 1843, Jean-Pierre Boyer quitta le pouvoir et partit en exil après des révoltes contre son gouvernement autoritaire.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Lequel des chefs d'Etats suivants dirigeait la Russie lors de l'effondrement du bloc socialiste en 1989": (
        "C",
        "Mikhaïl Gorbatchev dirigeait l'URSS lors des réformes de glasnost et perestroïka et de l'effondrement du bloc socialiste en 1989-1991.",
        "Histoire mondiale",
        "facile",
    ),
    "Lequel des groupes de personnages suivants a résisté à l'occupation américaine d'Haïti": (
        "B",
        "Charlemagne Péralte et Benoît Batraville ont mené la résistance armée contre l'occupation américaine (1915-1934), notamment via les Cacos.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "L'un des faits suivants prouve la détermination de 9 Toussaint Louverture": (
        "C",
        "La Constitution de 1801, promulguée par Toussaint Louverture, affirmait l'autonomie de Saint-Domingue tout en maintenant un lien nominal avec la France, révélant son projet d'autonomie politique.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Les deux principaux vainqueurs de la Deuxième guerre mondiale étaient": (
        "D",
        "Les États-Unis et l'URSS furent les deux grandes puissances victorieuses de 1945. Leur rivalité a ensuite structuré la guerre froide et la bipolarisation mondiale.",
        "Histoire mondiale",
        "facile",
    ),
    "Lequel des chefs d'État américains suivants a dirigé les États-Unis à la fin de la 1° guerre Mondiale": (
        "C",
        "Woodrow Wilson était président des États-Unis de 1913 à 1921 et a représenté son pays à la conférence de paix de Versailles en 1919.",
        "Histoire mondiale",
        "facile",
    ),
    "Laquelle des périodes suivantes correspond à la tenue de la conférence de Bandung": (
        "D",
        "La conférence de Bandung s'est tenue du 18 au 24 avril 1955 en Indonésie. Elle a réuni des pays afro-asiatiques pour promouvoir la solidarité et la décolonisation.",
        "Histoire mondiale",
        "moyen",
    ),
    "Parmi les pays suivants, lequel a pour capitale Bangui": (
        "B",
        "Bangui est la capitale de la République centrafricaine. Kinshasa est la capitale de la RDC, Pretoria est une capitale sud-africaine, Conakry celle de la Guinée.",
        "Géographie",
        "facile",
    ),
    "Le gouvernement de Jean Pierre Boyer a sucédé à celui de": (
        "C",
        "Jean-Pierre Boyer a succédé à Alexandre Pétion après sa mort en 1818 et a unifié les deux parties du pays en 1820.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "De tous ces artistes dignes de la culture haïtienne, lequel 1 est représentatif uniquement du théâtre": (
        "B",
        "Théodore Beaubrun, connu sous le nom de Languichatte, est une figure emblématique du théâtre haïtien comique. Nemours Jean-Baptiste est plutôt lié à la musique compas.",
        "Culture haïtienne",
        "moyen",
    ),
    "Tous ces facteurs constituent des obstacles au développe- ment économique d'Haïti sauf un": (
        "D",
        "L'essor du compas est un phénomène culturel et musical, pas un obstacle économique. La fuite des capitaux, le mépris du travail manuel et le brigandage agricole freinent le développement.",
        "Économie",
        "moyen",
    ),
    "Le système d'intégration centre-américain regroupe les pays suivants": (
        "A",
        "Le SICA (Système d'intégration centreaméricain) regroupe des pays comme le Panama, le Costa Rica et le Nicaragua. Haïti et Cuba ne font pas partie de cette intégration régionale.",
        "Géographie",
        "moyen",
    ),
    "La guerre mondiale a produit toutes ces conséquences sauf une": (
        "B",
        "La fin du colonialisme en Afrique s'accélère surtout dans les années 1950-1960, pas comme conséquence directe immédiate des guerres mondiales. L'ONU, le fascisme et la bipolarisation en sont des effets plus directs.",
        "Histoire mondiale",
        "moyen",
    ),
    "Tous ces faits ont provoqué l'émancipation des peuples latino-américains au 19° siècle, sauf un": (
        "B",
        "La doctrine Monroe (1823) visait à limiter l'intervention européenne dans les Amériques au profit des États-Unis, sans provoquer directement les indépendances latino-américaines.",
        "Histoire latino-américaine",
        "moyen",
    ),
    "Tous ces facteurs caractérisent les pays développés, excepté un": (
        "D",
        "« Indicateur de diminution humaine » n'existe pas comme critère de développement. L'IDH (Indice de développement humain) est en revanche un indicateur reconnu.",
        "Économie",
        "facile",
    ),
    "Après l'indépendance, Haïti fait face à tous ces problèmes, sauf un": (
        "A",
        "Après 1804, l'esclavage était aboli en Haïti : il ne s'agit donc pas d'un problème post-indépendance. L'isolement diplomatique, la ruine économique et les rivalités politiques étaient au contraire réels.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Le secteur industriel haïtien est actuellement confronté h|! toutes les difficultés suivantes, sauf une": (
        "A",
        "Haïti connaît un faible pouvoir d'achat, pas élevé. Le rejet des produits locaux, le manque de matières premières et la pénurie de capitaux sont des difficultés réelles de l'industrie.",
        "Économie",
        "moyen",
    ),
    "Le dialogue Nord-Sud est une expression utilisée couramment pour désigner les rapports entre": (
        "D",
        "Le dialogue Nord-Sud désigne les relations entre pays développés (Nord) et pays en voie de développement (Sud), notamment sur le plan économique et commercial.",
        "Économie",
        "facile",
    ),
    "La Caraïbe insulaire, désigne les îles baignées par la mer des Antilles": (
        "A",
        "La Caraïbe insulaire appartient géographiquement à l'Amérique du Nord, bien qu'on la situe culturellement dans la région caribéenne tropicale.",
        "Géographie",
        "moyen",
    ),
    "Laquelle de ces mesures peut être considérée comme une cause de l'assassinat de l'Empereur Jacques 1°": (
        "A",
        "La vérification des titres de propriété voulue par Dessalines mécontenta les grands propriétaires et l'oligarchie mulâtre, contribuant à sa chute en 1806.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Identifie le groupe de héros de l'indépendance d'Haiti": (
        "B",
        "Dessalines, Pétion et Christophe sont trois figures majeures de l'indépendance haïtienne. Bolívar et San Martín appartiennent à l'Amérique latino-américaine.",
        "Histoire d'Haïti",
        "facile",
    ),
    "Lequel des critères suivants n'identifie pas les pays pauvres": (
        "D",
        "Les pays pauvres manquent d'infrastructures (routes, électricité, télécommunications). Un fort taux d'analphabétisme et une faible industrialisation les caractérisent au contraire.",
        "Économie",
        "facile",
    ),
    "Le groupe de pays faisant partie de la Caraïbe dans la liste ci- dessous est": (
        "C",
        "Le Panama, la Colombie et le Venezuela bordent la mer des Caraïbes et font partie de la région caribéenne. Le Canada et l'Uruguay n'appartiennent pas à la Caraïbe.",
        "Géographie",
        "moyen",
    ),
    "Après son indépendance en 1825, l'Amérique espagnole devait faire face à tous ces problèmes sauf un": (
        "C",
        "L'arrivée massive de Noirs n'était pas un problème général des républiques hispaniques après leurs indépendances. Instabilité politique, crise économique et caudillos dominèrent cette période.",
        "Histoire latino-américaine",
        "moyen",
    ),
    "Lequel des faits suivants est une cause occasionnelle de la première guerre mondiale": (
        "A",
        "L'attentat de Sarajevo contre l'archiduc François-Ferdinand le 28 juin 1914 fut le déclencheur immédiat (cause occasionnelle) de la Première Guerre mondiale.",
        "Histoire mondiale",
        "facile",
    ),
    "L'un des faits ci-dessous marque l'histoire de I l'humanité après 1960": (
        "A",
        "La décolonisation de l'Afrique est un fait majeur après 1960. L'indépendance d'Haïti (1804), la révolution chinoise (1949) et l'effondrement de l'Empire russe (1917) sont antérieurs.",
        "Histoire mondiale",
        "facile",
    ),
    "Lequel des faits ci-dessous ne contribuent pas au triomphe de la révolution anti-esclavagiste": (
        "A",
        "La coalition entre les puissances coloniales (France, Espagne, Grande-Bretagne) combattit la révolution haïtienne ; elle ne contribua pas à son triomphe.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Le Néocolonialisme constitue un frein au développement | à des pays pauvres par le fait qu'il favorise": (
        "B",
        "Le néocolonialisme maintient une domination économique et politique des pays riches sur les pays pauvres, même après l'indépendance formelle.",
        "Économie",
        "moyen",
    ),
    "Laquelle des revendications suivantes formules par le Leader paysan Acaau a été d'ordre politique": (
        "D",
        "La revendication « un noir à la présidence » était explicitement politique. Les autres revendications des Piquets concernaient surtout la justice sociale et le partage des terres.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Laquelle de ces actions a traduit la politique du «Big Stick»": (
        "B",
        "La politique du « gros bâton » de Theodore Roosevelt se traduisit par des interventions militaires américaines dans la Caraïbe, dont l'occupation de la République dominicaine en 1916.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Lè Ameriken yo te okipe Ayiti nan lane 1915, yon evénman te pouse yo kite peyi a": (
        "B",
        "La résistance menée par Charlemagne Péralte et les Cacos a galvanisé l'opposition populaire à l'occupation américaine et a contribué à la pression pour le retrait des troupes en 1934.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Kilès nan sitiyasyon politik ak ekonomik sa yo ki te konsekans Okipasyon amerikèn an Ayiti": (
        "B",
        "L'occupation de 1915 a provoqué un réveil nationaliste et renforcé les valeurs africaines dans la résistance populaire haïtienne.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Ant fen 19° ak kômansman 20° syèk, kilès nan evénman 1 sa yo ki te febli enperyalis ewopeyen": (
        "C",
        "Le partage de l'Afrique et de l'Asie entre puissances européennes (impérialisme) a renforcé puis fragilisé les empires coloniaux, contribuant aux tensions qui ont mené à la Première Guerre mondiale.",
        "Histoire mondiale",
        "difficile",
    ),
    "Fen 19° ak kômansman 20° syèk, lè Ameriken t'ap mache anvayi peyi nan Karayib la, yo te vle": (
        "B",
        "Au début du XXe siècle, les États-Unis cherchaient surtout à contrôler les routes commerciales, les investissements et les ressources économiques dans la Caraïbe.",
        "Histoire d'Haïti",
        "moyen",
    ),
    "Tout eleman sa yo reskonsab tansyon sosyal nan Karayib la, sof youn": (
        "A",
        "Une consommation élevée de protéines n'est pas une cause de tension sociale. Le gaspillage, les faibles revenus et la mauvaise productivité agricole créent au contraire des tensions.",
        "Économie",
        "moyen",
    ),
    "Pou limanite chape anba lanm6, li dwe adopte youn nan konpôtman sa yo": (
        "C",
        "La conservation des ressources naturelles est indispensable pour assurer la durabilité de l'humanité. L'exploitation excessive des minéraux et la destruction des espèces nuisent à l'équilibre écologique.",
        "Environnement",
        "facile",
    ),
    # maths
    "L'orthocentre d'un triangle est le point de rencontre des": (
        "C",
        "L'orthocentre est le point d'intersection des hauteurs d'un triangle. Les bissectrices donnent le centre du cercle inscrit, les médianes le centre de gravité et les médiatrices le centre du cercle circonscrit.",
        "Géométrie",
        "facile",
    ),
    "La médiane de la série : 13 ; 21:19; 18:27; 15 est": (
        "C",
        "En ordonnant la série : 13, 15, 18, 19, 21, 27, la médiane est la moyenne des deux valeurs centrales : (18 + 19) / 2 = 18,5.",
        "Statistiques",
        "moyen",
    ),
    "Si une droite est sécante à un cercle, alors": (
        "A",
        "Une droite sécante coupe un cercle en deux points distincts. Une tangente n'a qu'un point commun, une droite extérieure n'en a aucun.",
        "Géométrie",
        "facile",
    ),
    "Diminuer x de 5% c'est multiplier x par": (
        "C",
        "Diminuer une quantité de 5 % revient à conserver 95 % de sa valeur, donc à multiplier par 0,95.",
        "Pourcentages",
        "facile",
    ),
    "La partie décimale du nombre — 5,32 est": (
        "C",
        "La partie décimale d'un nombre est la partie non entière de sa valeur absolue. Pour -5,32, la partie décimale est 0,32.",
        "Nombres réels",
        "moyen",
    ),
    "Le point de rencontre des médianes d'un triangle est l appelé": (
        "C",
        "Le point de concours des médianes d'un triangle est le centre de gravité, noté G. Il partage chaque médiane en ratio 2:1 à partir du sommet.",
        "Géométrie",
        "facile",
    ),
    "50% d'une somme d'argent représente": (
        "D",
        "50 % signifie la moitié d'une quantité. Par exemple, 50 % de 100 gourdes = 50 gourdes, soit la moitié.",
        "Pourcentages",
        "facile",
    ),
    "On donne H = 5x — 4x — 2x — 3x ; l'expression réduite L de H est": (
        "B",
        "H = 5x - 4x - 2x - 3x = (5 - 4 - 2 - 3)x = -4x. On regroupe les termes en x pour obtenir l'expression réduite.",
        "Algèbre",
        "facile",
    ),
    "Que peut-on dire d'un cercle et d'une droite qui n'ont aucun point commun": (
        "D",
        "Si une droite et un cercle n'ont aucun point commun, la droite est entièrement extérieure au cercle. La distance du centre à la droite est supérieure au rayon.",
        "Géométrie",
        "moyen",
    ),
    "Si deux cercles (C) et(C”) de centres respectifs O et O’ et de rayons respectifs r et r” sont tels que OO’ =r+r": (
        "C",
        "Quand la distance entre les centres est égale à la somme des rayons (OO' = r + r'), les deux cercles sont tangents extérieurement et se touchent en un seul point.",
        "Géométrie",
        "moyen",
    ),
    "Lequel de ces points est le point de rencontre des droites perpendiculaires aux côtés d'un triangle en leur milieu": (
        "A",
        "Les perpendiculaires aux côtés passant par leurs milieux sont les médiatrices. Leur point de concours est le centre du cercle circonscrit au triangle.",
        "Géométrie",
        "moyen",
    ),
    "Le point d'intersection des médiatrices d'un triangle est": (
        "C",
        "Les médiatrices d'un triangle se coupent au centre du cercle circonscrit, équidistant des trois sommets.",
        "Géométrie",
        "facile",
    ),
    "L'expression factorisée de f(x) = 4x? — 25 est": (
        "C",
        "4x² - 25 est une différence de deux carrés : (2x)² - 5² = (2x - 5)(2x + 5).",
        "Algèbre",
        "moyen",
    ),
    # svt
    "Au cours d'un accident, le fémur gauche d'un enfant est légèrement fissuré": (
        "B",
        "Une fêlure est une fracture partielle ou une fissure de l'os sans rupture complète. Une entorse touche les ligaments, une luxation un déplacement articulaire.",
        "Anatomie",
        "facile",
    ),
    "Une roche dans laquelle on extrait une substance métallique, à grande valeur économique, est": (
        "C",
        "Un minerai est une roche ou un dépôt naturel contenant un métal en quantité suffisante pour être exploité économiquement.",
        "Géologie",
        "facile",
    ),
    "Le maïs est une plainte à fleurs qui est classée parmi les": (
        "C",
        "Le maïs produit des fleurs et des graines : c'est un spermatophyte (plante à graines), plus précisément une angiosperme.",
        "Botanique",
        "facile",
    ),
    "L'inflammation de la membrane qui enveloppe les poumons est connue sous le nom de": (
        "C",
        "La plèvre est la membrane qui enveloppe les poumons. Son inflammation s'appelle la pléurésie. La pneumonie concerne surtout le tissu pulmonaire.",
        "Anatomie",
        "moyen",
    ),
    "Les cellules sanguines qui nous protègent en": (
        "B",
        "Les plaquettes (thrombocytes) s'agrègent lors d'une blessure pour former un clou plaquettaire et stopper le saignement.",
        "Biologie humaine",
        "facile",
    ),
    "L'une des actions suivantes est constatée lors de | — l'inspiration": (
        "A",
        "Lors de l'inspiration, le diaphragme se contracte et la cage thoracique s'agrandit, ce qui augmente le volume pulmonaire et fait entrer l'air.",
        "Physiologie",
        "facile",
    ),
    "L'une des unités ci-dessous est celle de la poussée d'Archimède": (
        "C",
        "La poussée d'Archimède est une force, donc s'exprime en newtons (N). Le joule mesure l'énergie, le pascal la pression et le watt la puissance.",
        "Physique",
        "facile",
    ),
    "Quelle est la force nécessaire pour soulever un objet 7 pesant 600 N à l'aide d'un système de six (6) poulies": (
        "D",
        "Avec 3 poulies mobiles, le gain mécanique est d'environ 6. La force nécessaire est 600 N / 6 = 100 N, en négligeant les frottements.",
        "Physique",
        "moyen",
    ),
    "L'une des mesures ci-dessous permet de diminuer | _ la pression": (
        "C",
        "La pression P = F/S. Pour diminuer la pression à force constante, il faut augmenter la surface de contact.",
        "Physique",
        "facile",
    ),
    "L'un des appareils ci-dessous permet de transformer l'énergie chimique en énergie électrique": (
        "D",
        "Une batterie (pile) convertit l'énergie chimique stockée dans ses réactifs en énergie électrique. Un ventilateur fait l'inverse en consommant l'électricité.",
        "Physique",
        "facile",
    ),
    "Le conduit qui amène l'urine des reins vers la vessie s'appelle": (
        "C",
        "L'uretère est le canal qui transporte l'urine des reins vers la vessie. L'urètre évacue l'urine vers l'extérieur.",
        "Anatomie",
        "facile",
    ),
    "Les nerfs crâniens sont au nombre de": (
        "A",
        "Le corps humain possède 12 paires de nerfs crâniens numérotés de I à XII, qui innervent principalement la tête et le cou.",
        "Anatomie",
        "facile",
    ),
    "La contraction de l'un de ses muscles fait augmenter le volume de la cage thoracique": (
        "D",
        "Le diaphragme, en se contractant, s'abaisse et augmente le volume de la cage thoracique lors de l'inspiration.",
        "Physiologie",
        "facile",
    ),
    "Chez la femme, après la fécondation, la cellule-œuf ya se développer dans": (
        "B",
        "Après la fécondation dans la trompe de Fallope, l'embryon migre et se développe principalement dans l'utérus où il s'implante.",
        "Biologie humaine",
        "facile",
    ),
    "Les impuretés qui accompagnent un minerai | _ s'appellent": (
        "B",
        "La gangue désigne les matériaux stériles et impuretés entourant le minerai dans le gisement et qu'il faut éliminer lors de l'extraction.",
        "Géologie",
        "facile",
    ),
    "La germination d'une spore de fougère donne | _ naissance premièrement": (
        "D",
        "La spore de fougère produit d'abord un prothalle (gamétophyte) aplati, qui portera ensuite les organes reproducteurs avant de donner la plante adulte.",
        "Botanique",
        "moyen",
    ),
    "L'un des effets ci-dessous explique la décomposition d'un corps": (
        "D",
        "La décomposition d'un corps est un processus chimique où les macromolécules organiques sont dégradées par des micro-organismes et des réactions biochimiques.",
        "Biologie",
        "facile",
    ),
    "Quelle est l'énergie potentielle d'une balle de ping- HE pong de masse 20 g qui tombe d'une hauteur de 3 m": (
        "C",
        "Ep = m × g × h = 0,020 kg × 10 N/kg × 3 m = 0,6 joule. Il faut convertir les grammes en kilogrammes.",
        "Physique",
        "moyen",
    ),
    "L'une des grandeurs ci-dessous permet d'augmenter ou de réduire la poussée d'Archimède": (
        "A",
        "La poussée d'Archimède dépend du volume de fluide déplacé. Modifier le volume de l'objet immergé change directement cette poussée.",
        "Physique",
        "moyen",
    ),
    "Toutes les actions suivantes engendrent la pollution, sauf une": (
        "D",
        "Les engrais organiques enrichissent le sol de manière durable et sont moins polluants que les engrais chimiques ou la combustion de plastiques.",
        "Environnement",
        "facile",
    ),
    "Le système nerveux central est constitué": (
        "B",
        "Le système nerveux central comprend l'encéphale (cerveau, cervelet, tronc cérébral) et la moelle épinière, qui coordonnent les activités de l'organisme.",
        "Anatomie",
        "facile",
    ),
    "Le chyme est un liquide qui résulte de la": (
        "A",
        "Le chyme est le mélange semi-liquide formé dans l'estomac à la suite de la digestion gastrique des aliments.",
        "Physiologie",
        "facile",
    ),
    "L'air qui sort des poumons suit le trajet suivant": (
        "C",
        "L'air expiré parcourt le trajet inverse de l'inspiration : bronches → trachée → larynx/pharynx → fosses nasales.",
        "Physiologie",
        "moyen",
    ),
    "Les tubes urinifères ont pour rôle d'assurer": (
        "C",
        "Les uretères (tubes urinifères) transportent l'urine produite par les reins vers la vessie.",
        "Anatomie",
        "facile",
    ),
    "Les tendons sont des capsules résistances qui ont pour rôle de": (
        "D",
        "Les tendons sont des tissus conjonctifs fibreux qui rattachent les muscles aux os et transmettent la force musculaire.",
        "Anatomie",
        "facile",
    ),
    "L'’inspiration est un mouvement respiratoire qui L entraine l'une des actions suivantes": (
        "D",
        "L'inspiration provoque la dilatation des poumons grâce à l'augmentation du volume thoracique, permettant l'entrée de l'air.",
        "Physiologie",
        "facile",
    ),
    "On parle de 'tendinie' dans l'un des cas suivants": (
        "C",
        "La tendinite est l'inflammation d'un tendon, souvent à la suite d'un effort répété ou d'un traumatisme.",
        "Santé",
        "facile",
    ),
    "Le sapin est classé parmi les phanérogames parce que la plante": (
        "B",
        "Le sapin est un gymnosperme (phanérogame) : il produit des graines nues, non enfermées dans un fruit charnu.",
        "Botanique",
        "moyen",
    ),
    "Laquelle des affirmations suivantes est fausse dans le cas des bryophytes": (
        "C",
        "Les bryophytes (mousses) n'ont pas de vraies racines, seulement des rhizoïdes. Elles possèdent chlorophylle, une tige et des feuilles.",
        "Botanique",
        "moyen",
    ),
    "La symbiose est une forme de vie observée dans l'un des cas suivants": (
        "C",
        "La symbiose est une association entre deux organismes qui tirent tous deux un bénéfice de leur relation.",
        "Biologie",
        "facile",
    ),
    "L'une des affirmations suivantes est vraie. Laquelle ? l": (
        "C",
        "Le riz pousse bien dans les sols marécageux et humides. Les sols sablonneux sont plutôt perméables et les sols perméables ne conviennent pas au riz.",
        "Géologie",
        "moyen",
    ),
    "L'utérus est un organe de l'un des appareils suivants": (
        "D",
        "L'utérus est l'organe de l'appareil reproducteur féminin où se développe l'embryon après la fécondation.",
        "Anatomie",
        "facile",
    ),
    "On parle de foulure ou d'enrtorse dans l'un des cas suivants": (
        "B",
        "Une foulure (entorse) correspond à l'étirement ou à la déchirure des ligaments autour d'une articulation.",
        "Santé",
        "facile",
    ),
    "Le manguier est classé parmi les angiospermes parce qu'il": (
        "C",
        "Le manguier produit des graines protégées à l'intérieur d'un fruit, caractéristique des angiospermes.",
        "Botanique",
        "facile",
    ),
    "Les éléments caractéristiques d'une force sont": (
        "B",
        "Une force est définie par son intensité, sa direction, son sens et son point d'application (origine).",
        "Physique",
        "facile",
    ),
    "La production du son dépend de trois facteurs appartenant à l'un des groupes suivants": (
        "D",
        "Pour qu'un son se produise et soit perçu, il faut une source vibrante, un milieu matériel élastique de propagation et un récepteur.",
        "Physique",
        "moyen",
    ),
    "Les glandes gastriques sont situées": (
        "D",
        "Les glandes gastriques se trouvent dans la paroi interne de l'estomac et sécrètent le suc gastrique (acide chlorhydrique et enzymes).",
        "Anatomie",
        "facile",
    ),
    "Le timbre est la qualité qui différencie les sons": (
        "B",
        "Le timbre permet de distinguer deux sons de même hauteur et même intensité, par exemple une flûte et un violon jouant la même note.",
        "Physique",
        "moyen",
    ),
    "Les artères sont des vaisseaux qui conduisent le sang": (
        "C",
        "Les artères transportent le sang du cœur vers les organes et les tissus. Les veines ramènent le sang vers le cœur.",
        "Anatomie",
        "facile",
    ),
    "La blennorragie se caractérise par l'apparition l": (
        "C",
        "La blennorragie (gonorrhée) est une infection sexuellement transmissible se manifestant par un écoulement urétral et des douleurs à la miction.",
        "Santé",
        "moyen",
    ),
    "Les lichens résultent de l'association": (
        "A",
        "Un lichen est une symbiose entre un champignon et une algue (ou une cyanobactérie) vivant en association étroite.",
        "Botanique",
        "facile",
    ),
    "Un être vivant autotrophe": (
        "C",
        "Un être autotrophe fabrique ses propres matières organiques à partir de matière minérale, grâce à la photosynthèse ou à la chimiosynthèse.",
        "Biologie",
        "facile",
    ),
    "La poussée exercée par un liquide sur un solide immergé est": (
        "C",
        "Selon le principe d'Archimède, la poussée exercée sur un corps immergé est égale au poids du volume de liquide déplacé.",
        "Physique",
        "moyen",
    ),
    "Un pied de malanga ne peut pas se reproduire par le semis parce que": (
        "C",
        "Le malanga se reproduit surtout végétativement par tubercule. Il ne produit pas de fleurs ni de graines permettant un semis classique.",
        "Botanique",
        "moyen",
    ),
    "La salive est secrétée par trois parties de glandes. Ce 8. sont les glandes": (
        "C",
        "La salive est produite par les glandes parotides, sous-maxillaires et sublinguales, situées dans la région buccale.",
        "Anatomie",
        "moyen",
    ),
    "Le sol se forme par": (
        "D",
        "Le sol se forme par altération et dégradation progressive des roches sous l'action de l'eau, du climat, du vent et des êtres vivants.",
        "Géologie",
        "facile",
    ),
    "Lequel de ces groupes de mots représente un ensemble de thallophytes": (
        "B",
        "Les thallophytes comprennent les organismes sans racines, tiges ni feuilles différenciées, comme les algues et les champignons.",
        "Botanique",
        "moyen",
    ),
    "Le chyle est le liquide blanchâtre contenu dans": (
        "C",
        "Le chyle est le liquide blanchâtre formé dans l'intestin grêle après la digestion et l'absorption des graisses.",
        "Physiologie",
        "moyen",
    ),
    # informatique
    "Quel sport a été conçu pour pratiquer à l'intérieur ct qui ne nécessiterait trop d'efforts physiques": (
        "B",
        "Le volleyball a été inventé en 1895 par William G. Morgan comme sport d'intérieur, moins intense que le basketball, accessible à un large public.",
        "Technologie",
        "facile",
    ),
}

FR_SKIP_PATTERNS = [
    "Le titre qui est approprié au texte",
    "Selon le texte, on se nourrit",
    "Pour bien fonctionner, le corps humain",
    "vitamine C",
    "D'après le texte, une surdose",
    "Dans le texte l'auteur",
    "Indique l'idée qui n'appartient pas au texte",
    "Dans le texte ci-dessus",
    "L'idée qui est en rapport avec le texte",
    "Dans le texte, on",
    "Les jeunes sont les plus grands consommateurs",
    "Dans ce texte, l'auteur",
    "D'après le texte, la concentration humaine",
    "D'après le texte on parle",
    "L'expression 'Partir à son moment'",
    "Dans le texte, Pierre vient",
    "Maître Casséus renvoyait",
    "À 19 ans, Ron commençait",
    "Le premier quotidien des temps modernes",
    "Lequel de ces groupes de facteurs climatiques",
]


def clean_option(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\s+\d+$", "", t)
    t = re.sub(r"\s+[°|_—\-=LDRPHTI]+$", "", t)
    t = re.sub(r"\s+[a-zA-Z]$", "", t)
    t = re.sub(r"\s+\|\s*\d.*$", "", t)
    t = re.sub(r"\s+\d+\s*$", "", t)
    t = re.sub(r"Theåtre", "Théâtre", t)
    t = re.sub(r"mamä", "mamá", t)
    t = re.sub(r"Paris 9", "Paris", t)
    return t.strip()


def normalize(text: str) -> str:
    t = text.strip()
    t = t.replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")
    t = t.replace("`", "'").replace("\u00b4", "'").replace("\u2032", "'")
    for ch in ("—", "–", "−", "‑"):
        t = t.replace(ch, "-")
    t = t.replace("?", " ")
    t = re.sub(r"\s+", " ", t)
    return t.lower()


def clean_question(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\s+\d+$", "", t)
    t = re.sub(r"\s+\|\s*\d.*$", "", t)
    t = re.sub(r"\s+\d+\s*$", "", t)
    return t.strip()


def match_answer(question: str) -> tuple[str, str, str, str] | None:
    q = normalize(clean_question(question))
    for prefix, data in ANSWERS.items():
        p = normalize(prefix)
        if q.startswith(p) or p in q:
            return data
    return None


def should_skip_fr(question: str) -> bool:
    q = clean_question(question)
    return any(p in q for p in FR_SKIP_PATTERNS)


def build_item(raw: dict, letter: str, explanation: str, category: str, difficulty: str) -> dict:
    opts = [clean_option(o) for o in raw["options"]]
    idx = ord(letter.upper()) - ord("A")
    return {
        "question": clean_question(raw["question"]),
        "options": opts,
        "correct": letter.upper(),
        "explanation": explanation,
        "category": category,
        "difficulty": difficulty,
        "timer_seconds": 30,
    }


def main() -> None:
    clean = json.loads(CLEAN.read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}

    for subject, items in clean.items():
        if subject == "anglais" and not items:
            continue
        solved: list[dict] = []
        seen: set[str] = set()
        for raw in items:
            if subject == "francais" and should_skip_fr(raw["question"]):
                continue
            ans = match_answer(raw["question"])
            if not ans:
                continue
            letter, explanation, category, difficulty = ans
            qkey = clean_question(raw["question"])[:80].lower()
            if qkey in seen:
                continue
            seen.add(qkey)
            solved.append(build_item(raw, letter, explanation, category, difficulty))
        if solved:
            out[subject] = solved

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for subj, qs in out.items():
        print(f"{subj}: {len(qs)}")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
