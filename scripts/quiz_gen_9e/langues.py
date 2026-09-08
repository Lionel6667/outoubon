"""Anglais, espagnol, kreyòl — QCM locaux."""
from __future__ import annotations

import random

from .common import make_q, shuffle_wrong_options, bank_to_questions

ANGLAIS = [
    ("Choose the correct form: She ___ to school every day.", "goes", ["go", "going", "gone"], "3rd person singular: goes."),
    ("Past tense of 'go':", "went", ["goed", "gone", "going"], "Irregular: went."),
    ("Plural of 'child':", "children", ["childs", "childes", "child"], "Irregular plural."),
    ("Opposite of 'hot':", "cold", ["warm", "heat", "boil"], "Antonym."),
    ("'The' is a:", "article", ["verb", "noun", "adverb"], "Definite article."),
    ("He is ___ doctor.", "a", ["an", "the", "some"], "Consonant sound: a."),
    ("She is ___ honest person.", "an", ["a", "the", "some"], "Vowel sound: an."),
    ("I have ___ apple.", "an", ["a", "the", "many"], "Apple starts with vowel sound."),
    ("They ___ playing now.", "are", ["is", "am", "be"], "Present continuous: are."),
    ("I ___ not like coffee.", "do", ["does", "am", "is"], "Negative: do not."),
    ("Does she ___ English?", "speak", ["speaks", "speaking", "spoke"], "Question with does."),
    ("We ___ yesterday.", "studied", ["study", "studies", "studying"], "Past simple."),
    ("She has ___ finished.", "already", ["yet", "never", "ever"], "Already with present perfect."),
    ("I have never ___ Paris.", "visited", ["visit", "visits", "visiting"], "Present perfect + past participle."),
    ("If it rains, we ___ stay home.", "will", ["would", "stayed", "staying"], "First conditional."),
    ("Comparative of 'big':", "bigger", ["biggest", "more big", "bigly"], "Comparative: -er."),
    ("Superlative of 'good':", "best", ["better", "goodest", "most good"], "Irregular: best."),
    ("She is ___ than me.", "taller", ["tall", "tallest", "more tall"], "Comparative with than."),
    ("This is the ___ book.", "best", ["better", "good", "bestest"], "Superlative."),
    ("Countable: How ___ apples?", "many", ["much", "lot", "few"], "Many for countables."),
    ("Uncountable: How ___ water?", "much", ["many", "few", "number"], "Much for uncountables."),
    ("Some / any: I don't have ___ money.", "any", ["some", "many", "few"], "Negative: any."),
    ("Preposition: The book is ___ the table.", "on", ["in", "at", "by"], "On = surface."),
    ("Preposition: She lives ___ Haiti.", "in", ["on", "at", "to"], "In + country."),
    ("Preposition: Meet me ___ 3 pm.", "at", ["in", "on", "by"], "At + time."),
    ("Preposition: Birthday is ___ Monday.", "on", ["in", "at", "by"], "On + day."),
    ("Modal: You ___ wear a uniform.", "must", ["musts", "musting", "musted"], "Must = obligation."),
    ("Modal: Can I ___ help?", "have", ["has", "having", "had"], "Can I have...?"),
    ("Passive: The house was ___.", "built", ["build", "building", "builded"], "Past passive."),
    ("Gerund: I enjoy ___.", "reading", ["read", "reads", "to read"], "Enjoy + gerund."),
    ("Infinitive: I want ___ learn.", "to", ["for", "at", "by"], "Want to + verb."),
    ("Question word: ___ is your name?", "What", ["Where", "When", "Who"], "What for name."),
    ("Question word: ___ do you live?", "Where", ["What", "Who", "Which"], "Where for place."),
    ("Question word: ___ is that man?", "Who", ["What", "Where", "When"], "Who for person."),
    ("Question word: ___ does the lesson start?", "When", ["Where", "Who", "What"], "When for time."),
    ("Possessive: This is ___ pen.", "my", ["me", "I", "mine"], "My + noun."),
    ("Possessive pronoun: That book is ___.", "mine", ["my", "me", "I"], "Mine = my book."),
    ("Object pronoun: She called ___.", "me", ["I", "my", "mine"], "Call + object."),
    ("Reflexive: He hurt ___.", "himself", ["him", "his", "he"], "Reflexive pronoun."),
    ("Adverb of frequency: I ___ go to church.", "often", ["oftenly", "many", "much"], "Often."),
    ("Adverb: She speaks ___.", "quickly", ["quick", "quicker", "quickness"], "Adverb: -ly."),
    ("Conjunction: I like tea ___ coffee.", "and", ["but", "or", "so"], "And joins similar."),
    ("Conjunction: tired ___ happy.", "but", ["and", "or", "so"], "But = contrast."),
    ("Conjunction: Study hard ___ you will pass.", "and", ["but", "or", "because"], "Result."),
    ("Because: Late ___ traffic.", "because of", ["because", "of because", "because to"], "Because of + noun."),
    ("Although: ___ it was cold, we went out.", "Although", ["Despite", "However", "Therefore"], "Although + clause."),
    ("Despite: ___ the rain, we played.", "Despite", ["Although", "However", "Because"], "Despite + noun."),
    ("Tag question: You are tired, ___?", "aren't you", ["are you", "don't you", "won't you"], "Tag matches."),
    ("Imperative: ___ the door!", "Close", ["Closes", "Closing", "Closed"], "Base form."),
    ("Negative imperative: ___ smoke here.", "Don't", ["Not", "Doesn't", "Isn't"], "Don't + base."),
    ("There is / are: ___ many students.", "There are", ["There is", "It is", "They are"], "Plural: there are."),
    ("Used to: I ___ play football.", "used to", ["use to", "using to", "uses to"], "Used to + base."),
    ("Would rather: I'd rather ___ home.", "stay", ["to stay", "staying", "stayed"], "Rather + base."),
    ("Too / enough: too ___ to eat.", "tired", ["tiring", "tire", "tires"], "Too + adjective."),
    ("So / such: It was ___ hot.", "so", ["such", "very much", "too much"], "So + adjective."),
    ("Each / every: ___ student has a book.", "Each", ["All", "Both", "Either"], "Each = individually."),
    ("Both: ___ of them are here.", "Both", ["All", "Every", "Each"], "Both = two."),
    ("Either: You can take ___ road.", "either", ["neither", "both", "all"], "Either = one of two."),
    ("Neither: ___ answer is correct.", "Neither", ["Either", "Both", "All"], "Neither = not either."),
    ("Phrasal: Turn ___ the light.", "on", ["in", "at", "by"], "Turn on."),
    ("Phrasal: Give ___ my book.", "back", ["up", "off", "away"], "Give back."),
    ("Phrasal: She woke ___ early.", "up", ["on", "in", "at"], "Wake up."),
    ("Synonym of 'happy':", "glad", ["sad", "angry", "tired"], "Glad ≈ happy."),
    ("Antonym of 'begin':", "end", ["start", "open", "continue"], "End vs begin."),
    ("Synonym of 'big':", "large", ["small", "tiny", "little"], "Large ≈ big."),
    ("Spelling: correct word", "receive", ["recieve", "receeve", "receve"], "I before E except after C."),
    ("Homophone: I / eye / ___.", "aye", ["hay", "hey", "high"], "Sound alike."),
    ("British vs US: colour (UK) = ___ (US)", "color", ["coler", "coulor", "colur"], "US spelling."),
    ("American: truck = British ___", "lorry", ["car", "bus", "van"], "Lorry."),
    ("Idiom: It's raining cats and ___.", "dogs", ["birds", "fish", "mice"], "Heavy rain."),
    ("Idiom: Break a ___!", "leg", ["arm", "hand", "foot"], "Good luck."),
    ("Proverb: Honesty is the best ___.", "policy", ["police", "politic", "politics"], "Policy."),
    ("Vocabulary: A person who flies a plane:", "pilot", ["driver", "sailor", "chef"], "Pilot."),
    ("Vocabulary: Doctor works in a ___.", "hospital", ["school", "factory", "farm"], "Hospital."),
    ("Vocabulary: Library is for ___.", "books", ["food", "cars", "shoes"], "Books."),
    ("Vocabulary: Bakery sells ___.", "bread", ["medicine", "shoes", "tools"], "Bread."),
    ("Vocabulary: Opposite seasons: summer / ___", "winter", ["spring", "autumn", "fall"], "Winter."),
    ("Vocabulary: Elephant is ___.", "big", ["small", "tiny", "short"], "Big animal."),
    ("Vocabulary: Knife is used to ___.", "cut", ["write", "read", "sleep"], "Cut."),
    ("Grammar: noun", "person, place or thing", ["only action", "only color", "only number"], "Noun definition."),
    ("Grammar: verb shows", "action or state", ["only color", "only place", "only number"], "Verb."),
    ("Grammar: adjective describes", "noun", ["verb", "adverb", "pronoun"], "Adjective."),
    ("Grammar: adverb modifies", "verb/adjective", ["only noun", "only number", "only article"], "Adverb."),
    ("Tense: I eat every day =", "simple present", ["past", "future", "perfect"], "Habit."),
    ("Tense: I am eating =", "present continuous", ["simple past", "future", "perfect"], "Now."),
    ("Tense: I will eat =", "simple future", ["present", "past", "perfect"], "Will."),
    ("Tense: I have eaten =", "present perfect", ["simple past", "future", "continuous"], "Have + past participle."),
    ("Word order: SVO means", "Subject Verb Object", ["Verb Subject Object", "Object Verb Subject", "Verb Object Subject"], "English SVO."),
    ("Capitalize: days of week?", "Yes", ["No", "Sometimes never", "Only Sunday"], "Capitalize days."),
    ("Punctuation: end of sentence", ".", [",", ";", ":"], "Period."),
    ("Punctuation: list items", ",", [".", "?", "!"], "Comma."),
    ("Punctuation: question", "?", [".", ",", ";"], "Question mark."),
    ("Reading: main idea =", "central message", ["random detail", "page number", "font size"], "Main idea."),
    ("Reading: inference =", "logical guess", ["copy exact words", "ignore text", "random"], "Inference."),
]

ESPAGNOL = [
    ("Artículo: ___ casa (femenino)", "la", ["el", "los", "un"], "La casa."),
    ("Artículo: ___ libro (masculino)", "el", ["la", "las", "una"], "El libro."),
    ("Ser: Yo ___ estudiante.", "soy", ["es", "son", "estoy"], "Yo soy."),
    ("Estar: Yo ___ cansado.", "estoy", ["soy", "es", "son"], "Estar = state."),
    ("Plural: los ___", "niños", ["niño", "niña", "niñas"], "Niños plural."),
    ("Tener: Yo ___ dos hermanos.", "tengo", ["tiene", "tenemos", "tienen"], "Yo tengo."),
    ("Ir: Nosotros ___ al parque.", "vamos", ["va", "van", "ir"], "Vamos."),
    ("Hacer: ¿Qué ___ tú?", "haces", ["hace", "hacen", "hago"], "Tú haces."),
    ("Poder: No ___ entrar.", "puedo", ["puede", "pueden", "podemos"], "No puedo."),
    ("Querer: ___ agua.", "Quiero", ["Quieres", "Quiere", "Queremos"], "Quiero."),
    ("Gustar: Me ___ la música.", "gusta", ["gustan", "gusto", "gustas"], "Me gusta."),
    ("Preposición: Vivo ___ México.", "en", ["a", "de", "con"], "En + país."),
    ("Preposición: Voy ___ la escuela.", "a", ["en", "de", "con"], "A + place."),
    ("De: Soy ___ Haití.", "de", ["en", "a", "con"], "De Haití."),
    ("Con: Hablo ___ mi amigo.", "con", ["de", "en", "a"], "Con = with."),
    ("Por: Gracias ___ todo.", "por", ["para", "de", "en"], "Por gracias."),
    ("Para: Estudio ___ aprender.", "para", ["por", "de", "en"], "Para = purpose."),
    ("Question: ¿___ años tienes?", "Cuántos", ["Cuánto", "Cuánta", "Cuántas"], "Cuántos años."),
    ("Question: ¿___ es tu nombre?", "Cuál", ["Qué", "Quién", "Dónde"], "Cuál nombre."),
    ("Question: ¿___ vives?", "Dónde", ["Qué", "Quién", "Cuál"], "Dónde = where."),
    ("Question: ¿___ es él?", "Quién", ["Qué", "Dónde", "Cuál"], "Quién = who."),
    ("Question: ¿___ hora es?", "Qué", ["Cuál", "Quién", "Dónde"], "Qué hora."),
    ("Adjetivo: casa ___", "grande", ["grandes", "grandemente", "grandísimo"], "Grande."),
    ("Plural adj: chicas ___", "inteligentes", ["inteligente", "inteligent", "inteligenteses"], "Inteligentes."),
    ("Comparativo: más ___ que", "alto", ["alta", "altos", "altas"], "Más alto que."),
    ("Superlativo: el más ___", "rápido", ["rápidamente", "rápidos", "rápida"], "El más rápido."),
    ("Negación: No ___ nada.", "digo", ["digas", "dice", "dicen"], "No digo."),
    ("Reflexivo: Me ___ las manos.", "lavo", ["lava", "lavan", "lavamos"], "Me lavo."),
    ("Pretérito: Ayer ___ al cine.", "fuimos", ["vamos", "ir", "vayan"], "Fuimos past."),
    ("Imperfecto: Siempre ___ feliz.", "era", ["es", "fue", "son"], "Era = used to be."),
    ("Futuro: ___ mañana.", "Estudiaré", ["Estudio", "Estudiaba", "Estudié"], "Future."),
    ("Imperativo: ¡___ la ventana!", "Abre", ["Abres", "Abrir", "Abrió"], "Abre!"),
    ("Subjuntivo trigger: Espero que ___", "venga", ["viene", "vino", "venir"], "Espero que + subj."),
    ("Object: Veo ___", "a María", ["María", "de María", "con María"], "Personal a."),
    ("Demostrativo: ___ libro (here)", "este", ["ese", "aquel", "esta"], "Este = this."),
    ("Demostrativo: ___ casa (there far)", "aquella", ["esta", "ese", "aquel"], "Aquella."),
    ("Numero: 15 =", "quince", ["catorce", "dieciséis", "cincuenta"], "Quince."),
    ("Numero: 100 =", "cien", ["ciento", "mil", "diez"], "Cien."),
    ("Día: lunes, martes, ___", "miércoles", ["jueves", "viernes", "sábado"], "Sequence."),
    ("Mes: enero, febrero, ___", "marzo", ["abril", "mayo", "junio"], "March."),
    ("Color: el cielo es ___", "azul", ["rojo", "verde", "amarillo"], "Azul."),
    ("Animal: ___ ladra", "el perro", ["el gato", "el pájaro", "el ratón"], "Perro."),
    ("Comida: ___ con leche", "café", ["pan", "arroz", "pollo"], "Café."),
    ("Familia: madre del padre =", "abuela", ["tía", "hermana", "prima"], "Grandmother."),
    ("Profesión: enseña =", "profesor", ["doctor", "cocinero", "piloto"], "Teacher."),
    ("Lugar: donde compramos comida =", "supermercado", ["banco", "hospital", "iglesia"], "Supermarket."),
    ("Transporte: en la ___", "bicicleta", ["silla", "mesa", "ventana"], "Bicicleta."),
    ("Clima: hace ___ (hot)", "calor", ["frío", "sol", "nieve"], "Hace calor."),
    ("Clima: hace ___ (cold)", "frío", ["calor", "sol", "lluvia"], "Hace frío."),
    ("Verbo: hablar = to", "speak", ["eat", "sleep", "run"], "Speak."),
    ("Verbo: comer = to", "eat", ["drink", "sleep", "write"], "Eat."),
    ("Verbo: dormir = to", "sleep", ["eat", "run", "read"], "Sleep."),
    ("Verbo: escribir = to", "write", ["read", "eat", "run"], "Write."),
    ("Verbo: leer = to", "read", ["write", "eat", "run"], "Read."),
    ("Sinónimo: grande ≈", "grande", ["pequeño", "corto", "bajo"], "Same - big."),
    ("Antónimo: bueno / ___", "malo", ["bueno", "mejor", "buenísimo"], "Bad."),
    ("Antónimo: alto / ___", "bajo", ["alto", "grande", "largo"], "Short/low."),
    ("Palabra: gracias =", "thank you", ["hello", "goodbye", "please"], "Thanks."),
    ("Palabra: por favor =", "please", ["thanks", "sorry", "hello"], "Please."),
    ("Palabra: lo siento =", "I'm sorry", ["thanks", "hello", "goodbye"], "Sorry."),
    ("Palabra: hola =", "hello", ["goodbye", "thanks", "please"], "Hello."),
    ("Palabra: adiós =", "goodbye", ["hello", "thanks", "please"], "Goodbye."),
    ("Pregunta: ¿Hablas ___?", "español", ["española", "españoles", "españolas"], "Español language."),
    ("Tiempo: por la ___ (morning)", "mañana", ["noche", "tarde", "semana"], "Mañana."),
    ("Tiempo: por la ___ (night)", "noche", ["mañana", "tarde", "día"], "Noche."),
    ("Frecuencia: ___ día", "cada", ["todo", "muy", "poco"], "Cada día."),
    ("Comparación: tan ___ como", "inteligente", ["inteligentes", "inteligentemente", "inteligencia"], "Tan + adj."),
    ("Pronombre: ___ amigos (we)", "nosotros", ["ellos", "ustedes", "vosotros"], "We."),
    ("Pronombre: ___ casa (they fem)", "ellas", ["ellos", "nosotros", "ustedes"], "They fem."),
    ("Possessive: mi / tu / ___", "su", ["mis", "tus", "sus"], "His/her."),
    ("Diminutivo: perro →", "perrito", ["perros", "perra", "perrazo"], "Perrito."),
    ("Augmentativo: casa →", "casucha", ["casita", "casas", "casero"], "Casucha."),
    ("Gerundio: estoy ___", "comiendo", ["comer", "comí", "comeré"], "Comiendo."),
    ("Participio: he ___", "hablado", ["hablar", "hablo", "hablaba"], "Hablado."),
    ("Condicional: me ___", "gustaría", ["gusta", "gustó", "gustaba"], "Would like."),
    ("Voz pasiva: fue ___", "construido", ["construir", "construye", "construyó"], "Fue construido."),
    ("Conector: ___ estudias, aprendes.", "Si", ["Pero", "Porque", "Aunque"], "Si = if."),
    ("Conector: ___ llueve, no salgo.", "Si", ["Y", "O", "Pero"], "Si llueve."),
    ("Conector: estudio ___ paso.", "para", ["por", "de", "con"], "Para = in order to."),
    ("Vocabulario: la ___ (school)", "escuela", ["hospital", "tienda", "calle"], "School."),
    ("Vocabulario: el ___ (hospital)", "hospital", ["escuela", "parque", "cine"], "Hospital."),
    ("Vocabulario: la ___ (beach)", "playa", ["montaña", "ciudad", "campo"], "Beach."),
    ("Vocabulario: el ___ (mountain)", "montaña", ["playa", "río", "lago"], "Mountain."),
    ("Vocabulario: el ___ (river)", "río", ["montaña", "playa", "desierto"], "River."),
    ("Vocabulario: la ___ (city)", "ciudad", ["aldea", "país", "continente"], "City."),
    ("Vocabulario: el ___ (country)", "país", ["ciudad", "calle", "barrio"], "Country."),
    ("Vocabulario: la ___ (street)", "calle", ["país", "ciudad", "montaña"], "Street."),
    ("Vocabulario: el ___ (park)", "parque", ["calle", "país", "montaña"], "Park."),
    ("Vocabulario: la ___ (church)", "iglesia", ["escuela", "tienda", "fábrica"], "Church."),
    ("Vocabulario: el ___ (market)", "mercado", ["hospital", "iglesia", "parque"], "Market."),
    ("Vocabulario: la ___ (library)", "biblioteca", ["cafetería", "panadería", "carnicería"], "Library."),
    ("Vocabulario: el ___ (cinema)", "cine", ["teatro", "museo", "estadio"], "Cinema."),
    ("Vocabulario: la ___ (theater)", "teatro", ["cine", "museo", "estadio"], "Theater."),
    ("Vocabulario: el ___ (museum)", "museo", ["cine", "teatro", "estadio"], "Museum."),
    ("Vocabulario: la ___ (station)", "estación", ["aeropuerto", "puerto", "terminal"], "Station."),
    ("Vocabulario: el ___ (airport)", "aeropuerto", ["estación", "puerto", "terminal"], "Airport."),
    ("Vocabulario: la ___ (port)", "puerta", ["ventana", "mesa", "silla"], "Door - puerta."),
    ("Número ordinal: primero, segundo, ___", "tercero", ["cuarto", "quinto", "sexto"], "Third."),
    ("Tiempo: hoy, ayer, ___", "mañana", ["ahora", "siempre", "nunca"], "Tomorrow."),
    ("Tiempo: siempre / nunca / ___", "a veces", ["nunca", "siempre", "jamás"], "Sometimes."),
    ("Salud: estoy ___", "enfermo", ["enferma", "enfermos", "enfermas"], "Sick."),
    ("Salud: necesito un ___", "doctor", ["árbol", "coche", "libro"], "Doctor."),
    ("Deporte: jugar al ___", "fútbol", ["tenis", "baloncesto", "voleibol"], "Football."),
    ("Música: tocar la ___", "guitarra", ["mesa", "silla", "ventana"], "Guitar."),
    ("Ropa: llevo una ___", "camisa", ["mesa", "silla", "ventana"], "Shirt."),
    ("Ropa: zapatos en los ___", "pies", ["manos", "cabeza", "ojos"], "Feet."),
    ("Cuerpo: veo con los ___", "ojos", ["oidos", "nariz", "boca"], "Eyes."),
    ("Cuerpo: oigo con los ___", "oidos", ["ojos", "nariz", "boca"], "Ears."),
    ("Cuerpo: huelo con la ___", "nariz", ["boca", "ojo", "oreja"], "Nose."),
    ("Cuerpo: como con la ___", "boca", ["nariz", "ojo", "oreja"], "Mouth."),
]

KREYOL = [
    ("Bonjou =", "bonjour", ["au revoir", "merci", "pardon"], "Salutation."),
    ("Bonswa =", "bonsoir", ["bonjour", "bonne nuit", "salut"], "Soir."),
    ("Mèsi =", "merci", ["bonjour", "pardon", "oui"], "Thanks."),
    ("Padon =", "pardon", ["merci", "bonjour", "oui"], "Sorry."),
    ("Wi =", "oui", ["non", "peut-être", "jamais"], "Yes."),
    ("Non =", "non", ["oui", "si", "toujours"], "No."),
    ("Kijan ou ye? =", "Comment es-tu?", ["Où es-tu?", "Qui es-tu?", "Quand?"], "How are you?"),
    ("Mwen byen =", "Je suis bien", ["Je suis mal", "Je dors", "Je mange"], "I'm fine."),
    ("Mwen rele... =", "Je m'appelle...", ["J'ai...", "Je vais...", "Je fais..."], "My name is..."),
    ("Kote ou soti? =", "D'où viens-tu?", ["Où vas-tu?", "Quand?", "Pourquoi?"], "Where from?"),
    ("Mwen soti Ayiti =", "Je viens d'Haïti", ["Je vais Haïti", "Je quitte Haïti", "Je suis France"], "From Haiti."),
    ("Mwen pale kreyòl =", "Je parle créole", ["Je mange", "Je dors", "Je cours"], "I speak Creole."),
    ("Mwen pa konprann =", "Je ne comprends pas", ["Je comprends", "Je sais", "Je vois"], "Don't understand."),
    ("Tanpri =", "s'il te plaît", ["merci", "pardon", "bonjour"], "Please."),
    ("Ou ka ede m? =", "Peux-tu m'aider?", ["Où es-tu?", "Qui es-tu?", "Quand?"], "Can you help?"),
    ("Mwen grangou =", "J'ai faim", ["J'ai soif", "Je suis fatigué", "Je suis content"], "Hungry."),
    ("Mwen swè =", "J'ai soif", ["J'ai faim", "Je dors", "Je cours"], "Thirsty."),
    ("Mwen fatige =", "Je suis fatigué", ["Je suis content", "Je suis jeune", "Je suis riche"], "Tired."),
    ("Mwen kontan =", "Je suis content", ["Je suis triste", "Je suis malade", "Je suis fâché"], "Happy."),
    ("Mwen malad =", "Je suis malade", ["Je suis fort", "Je suis jeune", "Je suis riche"], "Sick."),
    ("Ki lè li ye? =", "Quelle heure est-il?", ["Quel jour?", "Quelle date?", "Quelle année?"], "What time?"),
    ("Jodi a =", "aujourd'hui", ["demain", "hier", "toujours"], "Today."),
    ("Demen =", "demain", ["hier", "aujourd'hui", "maintenant"], "Tomorrow."),
    ("Yè =", "hier", ["demain", "aujourd'hui", "toujours"], "Yesterday."),
    ("Lekòl =", "école", ["maison", "hôpital", "marché"], "School."),
    ("Kay =", "maison", ["école", "voiture", "livre"], "House."),
    ("Liv =", "livre", ["stylo", "table", "chaise"], "Book."),
    ("Pen =", "pain", ["eau", "viande", "fruit"], "Bread."),
    ("Dlo =", "eau", ["pain", "riz", "sucre"], "Water."),
    ("Manje =", "manger / nourriture", ["dormir", "courir", "lire"], "Eat/food."),
    ("Dòmi =", "dormir", ["manger", "courir", "lire"], "Sleep."),
    ("Li =", "lire / il/elle", ["écrire", "manger", "dormir"], "Read/he/she."),
    ("Ekri =", "écrire", ["lire", "manger", "dormir"], "Write."),
    ("Kouri =", "courir", ["marcher", "dormir", "manger"], "Run."),
    ("Mache =", "marcher / marché", ["courir", "voler", "nager"], "Walk/market."),
    ("Gwo =", "grand", ["petit", "court", "bas"], "Big."),
    ("Piti =", "petit", ["grand", "long", "haut"], "Small."),
    ("Bèl =", "beau/belle", ["laid", "petit", "court"], "Beautiful."),
    ("Move =", "méchant/mauvais", ["bon", "gentil", "doux"], "Bad."),
    ("Bon =", "bon", ["mauvais", "laid", "petit"], "Good."),
    ("Chofè =", "chauffeur", ["docteur", "professeur", "cuisinier"], "Driver."),
    ("Doktè =", "docteur", ["professeur", "chauffeur", "pilot"], "Doctor."),
    ("Pwofesè =", "professeur", ["docteur", "chauffeur", "pilot"], "Teacher."),
    ("Manman =", "mère", ["père", "frère", "sœur"], "Mother."),
    ("Papa =", "père", ["mère", "frère", "sœur"], "Father."),
    ("Frè =", "frère", ["sœur", "mère", "père"], "Brother."),
    ("Sè =", "sœur", ["frère", "mère", "père"], "Sister."),
    ("Zanmi =", "ami", ["ennemi", "étranger", "voisin"], "Friend."),
    ("Timoun =", "enfant", ["adulte", "vieux", "bébé seul"], "Child."),
    ("Granmoun =", "adulte / vieux", ["enfant", "bébé", "ado"], "Adult."),
    ("Rouj =", "rouge", ["bleu", "vert", "jaune"], "Red."),
    ("Ble =", "bleu", ["rouge", "vert", "noir"], "Blue."),
    ("Vèt =", "vert", ["rouge", "bleu", "jaune"], "Green."),
    ("Jòn =", "jaune", ["rouge", "bleu", "vert"], "Yellow."),
    ("Nwa =", "noir", ["blanc", "gris", "rose"], "Black."),
    ("Blan =", "blanc", ["noir", "gris", "rose"], "White."),
    ("Solèy =", "soleil", ["lune", "étoile", "nuage"], "Sun."),
    ("Lalin =", "lune", ["soleil", "étoile", "nuage"], "Moon."),
    ("Dlo k ap tonbe =", "pluie", ["neige", "vent", "soleil"], "Rain."),
    ("Van =", "vent", ["pluie", "neige", "soleil"], "Wind."),
    ("Mòn =", "montagne", ["mer", "rivière", "plage"], "Mountain."),
    ("Lanmè =", "mer", ["montagne", "rivière", "forêt"], "Sea."),
    ("Rivyè =", "rivière", ["mer", "montagne", "désert"], "River."),
    ("Bato =", "bateau", ["voiture", "avion", "train"], "Boat."),
    ("Machin =", "voiture", ["bateau", "avion", "train"], "Car."),
    ("Avyon =", "avion", ["voiture", "bateau", "train"], "Plane."),
    ("Lari =", "rue", ["maison", "école", "hôpital"], "Street."),
    ("Mache =", "marché", ["école", "hôpital", "église"], "Market."),
    ("Legliz =", "église", ["école", "hôpital", "marché"], "Church."),
    ("Lopital =", "hôpital", ["école", "marché", "église"], "Hospital."),
    ("Polis =", "police", ["armée", "école", "marché"], "Police."),
    ("Lajan =", "argent", ["temps", "eau", "pain"], "Money."),
    ("Tan =", "temps", ["argent", "eau", "pain"], "Time."),
    ("Travay =", "travail", ["repos", "jeu", "sommeil"], "Work."),
    ("Repo =", "repos", ["travail", "jeu", "course"], "Rest."),
    ("Jwèt =", "jeu", ["travail", "repos", "sommeil"], "Game."),
    ("Mizik =", "musique", ["danse", "peinture", "sculpture"], "Music."),
    ("Danse =", "danse", ["musique", "peinture", "sculpture"], "Dance."),
    ("Fèt =", "fête", ["travail", "guerre", "maladie"], "Party."),
    ("Kalandriyè =", "calendrier", ["horloge", "montre", "réveil"], "Calendar."),
    ("Revèy =", "réveil / rêve", ["calendrier", "montre", "horloge"], "Alarm/dream."),
    ("Semèn =", "semaine", ["jour", "mois", "année"], "Week."),
    ("Mwa =", "mois", ["jour", "semaine", "heure"], "Month."),
    ("Ane =", "année", ["jour", "heure", "minute"], "Year."),
    ("Lendi =", "lundi", ["mardi", "mercredi", "jeudi"], "Monday."),
    ("Janvye =", "janvier", ["février", "mars", "avril"], "January."),
    ("Nouvèl an =", "Nouvel An", ["Noël", "Pâques", "Carnaval"], "New Year."),
    ("Premye janvye =", "1er janvier", ["18 mai", "17 octobre", "25 décembre"], "Jan 1."),
    ("Endepandans Ayiti =", "1 janvier 1804", ["14 juillet 1789", "4 juillet 1776", "1492"], "Haiti independence."),
    ("Kreyòl se lang", "ofisyèl", ["etranjè", "obligatwa sèl", "entèdi"], "Official language."),
    ("Franse se lang", "ofisyèl tou", ["entèdi", "obligatwa sèl", "pa egziste"], "Also official."),
    ("M ap =", "je vais (futur proche)", ["je suis", "j'étais", "j'ai"], "Future marker."),
    ("M te =", "j'étais / j'ai (past)", ["je suis", "je vais", "j'aurai"], "Past marker."),
    ("Pa =", "ne pas / pas", ["oui", "si", "toujours"], "Negation."),
    ("Tou =", "tout / aussi", ["rien", "jamais", "personne"], "All/also."),
    ("Anpil =", "beaucoup", ["peu", "rien", "un peu"], "Many/much."),
    ("Ti kras =", "un peu", ["beaucoup", "tout", "rien"], "A little."),
    ("Kote? =", "où?", ["quand?", "qui?", "quoi?"], "Where?"),
    ("Kilè? =", "quand? / quelle heure?", ["où?", "qui?", "quoi?"], "When?"),
    ("Kiyès? =", "qui?", ["où?", "quand?", "quoi?"], "Who?"),
    ("Kisa? =", "quoi?", ["qui?", "où?", "quand?"], "What?"),
    ("Poukisa? =", "pourquoi?", ["comment?", "où?", "quand?"], "Why?"),
    ("Kòman? =", "comment?", ["pourquoi?", "où?", "quand?"], "How?"),
    ("Avèk =", "avec", ["sans", "pour", "de"], "With."),
    ("San =", "sans", ["avec", "pour", "de"], "Without."),
    ("Pou =", "pour", ["avec", "sans", "de"], "For."),
    ("Nan =", "dans / en", ["sur", "sous", "devant"], "In."),
    ("Sou =", "sur", ["dans", "sous", "devant"], "On."),
    ("Anba =", "sous", ["sur", "dans", "devant"], "Under."),
    ("Dèyè =", "derrière", ["devant", "sur", "dans"], "Behind."),
    ("Devàn =", "devant", ["derrière", "sous", "sur"], "In front."),
    ("Bò =", "côté / près de", ["loin", "haut", "bas"], "Side/near."),
    ("Tou pre =", "très près", ["très loin", "haut", "bas"], "Very near."),
    ("Trè =", "très", ["peu", "un peu", "pas"], "Very."),
    ("Plis =", "plus", ["moins", "peu", "rien"], "More."),
    ("Mwens =", "moins", ["plus", "beaucoup", "tout"], "Less."),
    ("Menm =", "même", ["différent", "autre", "nouveau"], "Same."),
    ("Diferan =", "différent", ["même", "identique", "égal"], "Different."),
    ("Nouvo =", "nouveau", ["vieux", "ancien", "usé"], "New."),
    ("Vye =", "vieux", ["nouveau", "jeune", "frais"], "Old."),
    ("Jwenn =", "trouver / obtenir", ["perdre", "cacher", "jeter"], "Find/get."),
    ("Pèdi =", "perdre", ["trouver", "gagner", "garder"], "Lose."),
    ("Gen =", "avoir / il y a", ["pas avoir", "rien", "zéro"], "Have/there is."),
    ("Pa gen =", "il n'y a pas", ["il y a", "beaucoup", "tout"], "There isn't."),
    ("Bezwen =", "besoin", ["richesse", "luxe", "jeu"], "Need."),
    ("Vle =", "vouloir", ["refuser", "dormir", "manger"], "Want."),
    ("Kapab =", "pouvoir / capable", ["incapable", "faible", "malade"], "Can/capable."),
    ("Dwe =", "devoir", ["vouloir", "pouvoir", "aimer"], "Must/owe."),
    ("Renmen =", "aimer", ["détester", "ignorer", "fuir"], "Love/like."),
    ("Pa renmen =", "ne pas aimer", ["aimer", "adorer", "préférer"], "Don't like."),
    ("Aprann =", "apprendre", ["oublier", "ignorer", "fuir"], "Learn."),
    ("Aprann leson =", "étudier la leçon", ["jouer", "dormir", "manger"], "Study lesson."),
    ("Reyinyon =", "réunion / examen", ["fête", "jeu", "repos"], "Meeting/exam."),
    ("Egzamen =", "examen", ["fête", "jeu", "repos"], "Exam."),
    ("Repons =", "réponse", ["question", "problème", "devinette"], "Answer."),
    ("Kesyon =", "question", ["réponse", "solution", "fin"], "Question."),
    ("Pwoblèm =", "problème", ["solution", "réponse", "fin"], "Problem."),
    ("Solisyon =", "solution", ["problème", "question", "erreur"], "Solution."),
    ("Erè =", "erreur", ["solution", "réponse", "succès"], "Error."),
    ("Siksè =", "succès", ["échec", "erreur", "perte"], "Success."),
    ("Echèk =", "échec", ["succès", "victoire", "gain"], "Failure."),
    ("Viktwa =", "victoire", ["défaite", "échec", "perte"], "Victory."),
    ("Defèt =", "défaite", ["victoire", "succès", "gain"], "Defeat."),
    ("Ekselan =", "excellent", ["mauvais", "médiocre", "nul"], "Excellent."),
    ("Bon nòt =", "bonne note", ["mauvaise note", "zéro", "échec"], "Good grade."),
    ("Klas =", "classe", ["maison", "rue", "marché"], "Class."),
    ("Desann =", "descendre", ["monter", "voler", "nager"], "Go down."),
    ("Monte =", "monter", ["descendre", "tomber", "sauter"], "Go up."),
    ("Antre =", "entrer", ["sortir", "fuir", "partir"], "Enter."),
    ("Soti =", "sortir / venir de", ["entrer", "rester", "dormir"], "Exit/from."),
    ("Rete =", "rester / arrêter", ["partir", "courir", "voler"], "Stay/stop."),
    ("Ale =", "aller", ["venir", "rester", "dormir"], "Go."),
    ("Vini =", "venir", ["aller", "partir", "fuir"], "Come."),
    ("Tounen =", "retourner", ["partir", "fuir", "dormir"], "Return."),
    ("Chita =", "s'asseoir", ["debout", "courir", "sauter"], "Sit."),
    ("Kanpe =", "debout / arrêter", ["s'asseoir", "courir", "dormir"], "Stand/stop."),
    ("Mande =", "demander", ["répondre", "ignorer", "fuir"], "Ask."),
    ("Reponn =", "répondre", ["demander", "ignorer", "fuir"], "Answer."),
    ("Rakonte =", "raconter", ["écouter", "ignorer", "fuir"], "Tell."),
    ("Koute =", "écouter", ["parler", "ignorer", "fuir"], "Listen."),
    ("Pale =", "parler", ["écouter", "dormir", "manger"], "Speak."),
    ("Chante =", "chanter", ["danser", "manger", "dormir"], "Sing."),
    ("Gade =", "regarder", ["écouter", "dormir", "manger"], "Look."),
    ("Sonje =", "se souvenir", ["oublier", "ignorer", "fuir"], "Remember."),
    ("Bliye =", "oublier", ["se souvenir", "savoir", "voir"], "Forget."),
    ("Konprann =", "comprendre", ["ignorer", "fuir", "dormir"], "Understand."),
    ("Ede =", "aider", ["blesser", "ignorer", "fuir"], "Help."),
    ("Pwoteje =", "protéger", ["attaquer", "blesser", "nuire"], "Protect."),
    ("Respekte =", "respecter", ["insulter", "ignorer", "nuire"], "Respect."),
    ("Onè =", "honnête", ["menteur", "cruel", "méchant"], "Honest."),
    ("Manti =", "mensonge", ["vérité", "honnête", "sincère"], "Lie."),
    ("Vre =", "vrai", ["faux", "mensonge", "fictif"], "True."),
    ("Fo =", "faux", ["vrai", "correct", "exact"], "False."),
    ("Kò =", "corps", ["tête", "âme", "esprit seul"], "Body."),
    ("Tèt =", "tête", ["pied", "main", "dos"], "Head."),
    ("Men =", "main", ["pied", "tête", "dos"], "Hand."),
    ("Pye =", "pied", ["main", "tête", "dos"], "Foot."),
    ("Dlo nan je =", "larmes", ["pluie", "sueur", "rosée"], "Tears."),
    ("Dlo nan bouch =", "salive", ["pluie", "larmes", "rosée"], "Saliva."),
    ("Kè =", "cœur / courage", ["tête", "pied", "main"], "Heart/courage."),
    ("Lanmò =", "mort", ["vie", "naissance", "croissance"], "Death."),
    ("Lavi =", "vie", ["mort", "fin", "échec"], "Life."),
    ("Libète =", "liberté", ["prison", "chaîne", "esclavage"], "Freedom."),
    ("Dwa =", "droit", ["devoir seul", "interdiction", "crime"], "Right."),
    ("Devwa =", "devoir", ["droit seul", "privilège", "luxe"], "Duty."),
    ("Lalwa =", "la loi", ["crime", "chaos", "guerre"], "Law."),
    ("Kriminèl =", "criminel", ["honnête", "innocent", "juste"], "Criminal."),
    ("Jis =", "juste", ["injuste", "cruel", "méchant"], "Just."),
    ("Enjis =", "injuste", ["juste", "équitable", "honnête"], "Unjust."),
    ("Pè =", "père religieux / peur", ["mère", "frère", "ami"], "Priest/fear."),
    ("Lidè =", "leader", ["suiveur", "ennemi", "étranger"], "Leader."),
    ("Patriyot =", "patriote", ["traître", "ennemi", "étranger"], "Patriot."),
    ("Ayisyen =", "haïtien", ["étranger", "ennemi", "touriste"], "Haitian."),
    ("Ayiti =", "Haïti", ["France", "Cuba", "Jamaïque"], "Haiti."),
    ("Karayib =", "Caraïbes", ["Europe", "Asie", "Afrique"], "Caribbean."),
    ("Amerik =", "Amérique", ["Europe", "Asie", "Afrique"], "America."),
    ("Latè =", "Terre / monde", ["lune", "mars", "soleil"], "Earth."),
    ("Latè ap chofe =", "la Terre se réchauffe", ["la Terre se refroidit", "la Terre disparaît", "la Terre arrête"], "Global warming."),
    ("Plantasyon =", "plantation", ["usine", "école", "hôpital"], "Plantation."),
    ("Rekòt =", "récolte", ["semence", "pluie", "vent"], "Harvest."),
    ("Plant =", "planter", ["cueillir", "manger", "dormir"], "Plant."),
    ("Semans =", "semence", ["récolte", "fruit", "feuille"], "Seed."),
    ("Fwi =", "fruit", ["légume", "viande", "pain"], "Fruit."),
    ("Legim =", "légume", ["fruit", "viande", "pain"], "Vegetable."),
    ("Vyann =", "viande", ["fruit", "légume", "pain"], "Meat."),
    ("Riz =", "riz", ["pain", "eau", "sucre"], "Rice."),
    ("Diri =", "riz (Haïti)", ["pain", "eau", "sucre"], "Rice Creole."),
    ("Pwa =", "pois / haricots", ["riz", "pain", "eau"], "Beans."),
    ("Kole =", "riz et sauce", ["pain", "eau", "fruit"], "Rice and beans."),
    ("Soup joumou =", "soupe de giraumon (indépendance)", ["soupe tomate", "soupe poulet", "soupe légume"], "Independence soup."),
    ("Giraumon =", "giraumon / courge", ["tomate", "pomme", "orange"], "Pumpkin/squash."),
    ("Kreyon =", "crayon", ["stylo", "gomme", "règle"], "Pencil."),
    ("Plim =", "stylo (plume)", ["crayon", "gomme", "règle"], "Pen."),
    ("Gom =", "gomme", ["crayon", "stylo", "règle"], "Eraser."),
    ("Règ =", "règle", ["crayon", "stylo", "gomme"], "Ruler."),
    ("Kalkilatè =", "calculatrice", ["crayon", "stylo", "gomme"], "Calculator."),
    ("Konpitè =", "ordinateur", ["téléphone", "radio", "télé"], "Computer."),
    ("Telefòn =", "téléphone", ["ordinateur", "radio", "télé"], "Phone."),
    ("Entènèt =", "internet", ["radio", "télé", "journal"], "Internet."),
    ("Rezo sosyal =", "réseau social", ["journal", "livre", "crayon"], "Social network."),
    ("Foto =", "photo", ["vidéo", "audio", "texte"], "Photo."),
    ("Videyo =", "vidéo", ["photo", "audio", "texte"], "Video."),
    ("Chanjman klimatik =", "changement climatique", ["changement politique", "changement musical", "changement sportif"], "Climate change."),
    ("Polisyon =", "pollution", ["propreté", "pureté", "clarté"], "Pollution."),
    ("Pwoteje anviwonman =", "protéger l'environnement", ["polluer", "détruire", "brûler"], "Protect environment."),
    ("Reciklaj =", "recyclage", ["déchets", "pollution", "brûlage"], "Recycling."),
    ("Enerji =", "énergie", ["fatigue", "repos", "sommeil"], "Energy."),
    ("Elektrisite =", "électricité", ["eau", "air", "terre"], "Electricity."),
    ("Pèt dife =", "coupure de courant", ["fête", "jeu", "repos"], "Power outage."),
    ("EDH =", "compagnie électricité Haïti", ["école", "hôpital", "banque"], "EDH."),
    ("Dola =", "dollar", ["gourde", "euro", "peso"], "Dollar."),
    ("Goud =", "gourde haïtienne", ["dollar", "euro", "peso"], "Gourde."),
    ("Pri =", "prix", ["temps", "eau", "air"], "Price."),
    ("Mache pri =", "marché / prix monte", ["baisse", "stable", "zéro"], "Market/price."),
    ("Ekonomi =", "économie", ["politique", "sport", "musique"], "Economy."),
    ("Politik =", "politique", ["économie", "sport", "musique"], "Politics."),
    ("Eleksyon =", "élection", ["fête", "jeu", "repos"], "Election."),
    ("Vòt =", "vote", ["jeu", "fête", "repos"], "Vote."),
    ("Prezidan =", "président", ["maire", "professeur", "docteur"], "President."),
    ("Depatman =", "département", ["commune", "quartier", "rue"], "Department."),
    ("Komin =", "commune", ["département", "rue", "maison"], "Commune."),
    ("Katye =", "quartier", ["département", "commune", "pays"], "Neighborhood."),
    ("Pòtoprens =", "Port-au-Prince", ["Cap-Haïtien", "Jacmel", "Les Cayes"], "Capital."),
    ("Kap Ayisyen =", "Cap-Haïtien", ["Port-au-Prince", "Jacmel", "Les Cayes"], "Cap-Haitien."),
    ("Jakmèl =", "Jacmel", ["Port-au-Prince", "Cap-Haïtien", "Les Cayes"], "Jacmel."),
    ("Okay =", "Les Cayes", ["Port-au-Prince", "Cap-Haïtien", "Jacmel"], "Les Cayes."),
    ("Mòn Kabrit =", "Morne à l'UNESCO", ["Tour Eiffel", "Big Ben", "Colisée"], "Haiti UNESCO."),
    ("Sitadèl =", "Citadelle Laferrière", ["Tour Eiffel", "Big Ben", "Colisée"], "Citadel."),
    ("Sansoussi =", "Palais Sans-Souci", ["Tour Eiffel", "Big Ben", "Colisée"], "Sans-Souci."),
    ("Karnaval =", "carnaval", ["examen", "travail", "guerre"], "Carnival."),
    ("Rara =", "fête musicale haïtienne", ["carnaval seul", "examen", "travail"], "Rara."),
    ("Vodou =", "religion syncrétique haïtienne", ["religion européenne seule", "sport", "jeu"], "Vodou."),
    ("Kreyòl ayisyen =", "langue nationale", ["langue étrangère", "langue morte", "langue inventée"], "National language."),
    ("Alfabè =", "alphabétisation", ["analphabétisme", "ignorance", "fuite"], "Literacy."),
    ("Li ak ekri =", "lire et écrire", ["manger et dormir", "courir et sauter", "chanter et danser"], "Read and write."),
    ("9e ane =", "9e année fondamentale", ["université", "doctorat", "maternelle"], "9th grade."),
    ("Fondamantal =", "fondamental (école)", ["universitaire", "professionnel", "militaire"], "Fundamental school."),
    ("NS4 =", "nouveau secondaire (4 ans)", ["fondamental", "université", "maternelle"], "NS4 secondary."),
    ("Bak =", "baccalauréat", ["examen fondamental", "maternelle", "primaire"], "Baccalaureate."),
    ("OU TOU BON =", "plateforme éducative haïtienne", ["restaurant", "hôtel", "banque"], "Educational platform."),
]


def _from_bank(bank: list, category: str) -> list[dict]:
    return bank_to_questions(bank, category, "moyen")


def _prog_anglais(rng: random.Random) -> list[dict]:
    verbs = [
        ("go", "goes", "went", "gone"),
        ("see", "sees", "saw", "seen"),
        ("take", "takes", "took", "taken"),
        ("write", "writes", "wrote", "written"),
        ("eat", "eats", "ate", "eaten"),
        ("drink", "drinks", "drank", "drunk"),
        ("run", "runs", "ran", "run"),
        ("swim", "swims", "swam", "swum"),
        ("give", "gives", "gave", "given"),
        ("know", "knows", "knew", "known"),
        ("think", "thinks", "thought", "thought"),
        ("buy", "buys", "bought", "bought"),
        ("speak", "speaks", "spoke", "spoken"),
        ("break", "breaks", "broke", "broken"),
        ("choose", "chooses", "chose", "chosen"),
    ]
    out = []
    for base, s3, past, part in verbs:
        q = f"Past tense of '{base}':"
        wrongs = [s3, part, base + "ed"]
        opts, ci = shuffle_wrong_options(past, wrongs, rng)
        out.append(make_q(q, opts, ci, f"Past of {base} is {past}.", "Verbes", "facile"))
        q2 = f"She ___ to school every day. (verb: {base})"
        wrongs2 = [past, part, base + "ing"]
        opts2, ci2 = shuffle_wrong_options(s3, wrongs2, rng)
        out.append(make_q(q2, opts2, ci2, f"3rd person: {s3}.", "Grammaire", "facile"))
    vocab = [
        ("happy", "glad"), ("big", "large"), ("small", "tiny"), ("fast", "quick"),
        ("sad", "unhappy"), ("begin", "start"), ("end", "finish"), ("child", "kid"),
        ("mother", "mom"), ("father", "dad"), ("friend", "pal"), ("beautiful", "pretty"),
    ]
    for word, syn in vocab:
        q = f"Synonym of '{word}':"
        wrongs = [w for w, s in vocab if w != word][:3]
        opts, ci = shuffle_wrong_options(syn, wrongs, rng)
        out.append(make_q(q, opts, ci, f"{syn} is similar to {word}.", "Vocabulaire", "facile"))
    for _ in range(100):
        a, b = rng.randint(2, 40), rng.randint(2, 40)
        q = f"What is {a} + {b}?"
        correct = str(a + b)
        wrongs = [str(a + b + 1), str(a + b - 1), str(a * b)]
        opts, ci = shuffle_wrong_options(correct, wrongs, rng)
        out.append(make_q(q, opts, ci, f"{a}+{b}={correct}.", "Calcul", "facile"))
    for _ in range(60):
        a, b = rng.randint(2, 15), rng.randint(2, 12)
        q = f"What is {a} x {b}?"
        correct = str(a * b)
        wrongs = [str(a * b + 2), str(a * b - 1), str(a + b)]
        opts, ci = shuffle_wrong_options(correct, wrongs, rng)
        out.append(make_q(q, opts, ci, f"{a}×{b}={correct}.", "Calcul", "facile"))
    return out


def _prog_espagnol(rng: random.Random) -> list[dict]:
    out = []
    verbs = [
        ("hablar", "hablo", "hablas", "habla"),
        ("comer", "como", "comes", "come"),
        ("vivir", "vivo", "vives", "vive"),
        ("ser", "soy", "eres", "es"),
        ("estar", "estoy", "estas", "esta"),
        ("tener", "tengo", "tienes", "tiene"),
        ("ir", "voy", "vas", "va"),
        ("hacer", "hago", "haces", "hace"),
    ]
    for inf, yo, tu, el in verbs:
        q = f"Yo ___ (verb {inf})"
        wrongs = [tu, el, inf]
        opts, ci = shuffle_wrong_options(yo, wrongs, rng)
        out.append(make_q(q, opts, ci, f"Yo {yo}.", "Conjugaison", "facile"))
    nums = [(1, "uno"), (2, "dos"), (3, "tres"), (4, "cuatro"), (5, "cinco"),
            (6, "seis"), (7, "siete"), (8, "ocho"), (9, "nueve"), (10, "diez")]
    for n, word in nums:
        q = f"El numero {n} en espanol:"
        wrongs = [w for _, w in nums if w != word]
        rng.shuffle(wrongs)
        opts = [word] + wrongs[:3]
        rng.shuffle(opts)
        out.append(make_q(q, opts, opts.index(word), f"{n} = {word}.", "Nombres", "facile"))
    for a in range(2, 13):
        for b in range(2, 13):
            q = f"En espanol: {a} x {b} = ?"
            correct = str(a * b)
            wrongs = [str(a * b + 1), str(a * b - 1), str(a + b)]
            opts, ci = shuffle_wrong_options(correct, wrongs, rng)
            out.append(make_q(q, opts, ci, f"{a}x{b}={correct}.", "Calcul", "facile"))
    for a in range(10, 60):
        for b in range(1, 20):
            q = f"En espanol: {a} + {b} = ?"
            correct = str(a + b)
            wrongs = [str(a + b + 1), str(a + b - 1), str(a - b)]
            opts, ci = shuffle_wrong_options(correct, wrongs, rng)
            out.append(make_q(q, opts, ci, f"{a}+{b}={correct}.", "Calcul", "facile"))
    return out


def _prog_kreyol(rng: random.Random) -> list[dict]:
    out = []
    pairs = [
        ("bonjou", "bonjour"), ("bonswa", "bonsoir"), ("mèsi", "merci"), ("padon", "pardon"),
        ("kay", "maison"), ("lekòl", "école"), ("liv", "livre"), ("dlo", "eau"),
        ("manje", "manger"), ("dòmi", "dormir"), ("kouri", "courir"), ("mache", "marcher"),
    ]
    for kr, fr in pairs:
        q = f"'{kr}' signifie en français:"
        wrongs = [f for _, f in pairs if f != fr][:3]
        opts, ci = shuffle_wrong_options(fr, wrongs, rng)
        out.append(make_q(q, opts, ci, f"{kr} = {fr}.", "Vocabulaire", "facile"))
    for _ in range(80):
        n = rng.randint(5, 50)
        q = f"Kalkile: {n} + {rng.randint(1, 20)} = ?"
        a = rng.randint(1, 20)
        correct = str(n + a)
        wrongs = [str(n + a + 1), str(n + a - 1), str(n + a + 5)]
        opts, ci = shuffle_wrong_options(correct, wrongs, rng)
        out.append(make_q(q, opts, ci, f"Addition = {correct}.", "Calcul", "facile"))
    return out


def generate_anglais(rng: random.Random, seen: set[str]) -> list[dict]:
    out = _from_bank(ANGLAIS, "Anglais") + _prog_anglais(rng)
    rng.shuffle(out)
    return out


def generate_espagnol(rng: random.Random, seen: set[str]) -> list[dict]:
    out = _from_bank(ESPAGNOL, "Espagnol") + _prog_espagnol(rng)
    rng.shuffle(out)
    return out


def generate_kreyol(rng: random.Random, seen: set[str]) -> list[dict]:
    out = _from_bank(KREYOL, "Kreyòl") + _prog_kreyol(rng)
    rng.shuffle(out)
    return out
