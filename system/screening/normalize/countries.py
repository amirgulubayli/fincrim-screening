"""Country normalisation: codes, names and nationalities -> one canonical name.

SOP P6 location: codes and nationalities are countries; territories count as
their own country (Hong Kong, Puerto Rico, Jersey ... are NOT folded into China,
the United States or the United Kingdom). "International", "Europe" and other
regions are not countries.

Safety: a wrong country can cause an unsafe LOCATION_MISMATCH clear, so nothing
here guesses. A value that is not in the table, or that could mean more than one
country ("Congo", "Korean", "Virgin Islands", "Yugoslavia"), comes out as None.
Callers must not read None as "no country": use `classify_country` to tell an
empty / not-a-country value from an unrecognised one, and abstain (REVIEW) when
any value on either side is "unknown" or "ambiguous".

Public API
  normalize_country(raw)  -> canonical name | None
  classify_country(raw)   -> (canonical | None, status)
                             status: country | empty | not_country | ambiguous | unknown
  countries_in_text(text) -> tuple of canonical names found in free text
  find_countries(text)    -> same, as a list
  CANONICAL_COUNTRIES     -> frozenset of every canonical name
"""

from __future__ import annotations

import re
import unicodedata

from .names import strip_accents

# Each row: canonical name, ISO alpha-2, ISO alpha-3, alternative names, demonyms /
# adjectives. Codes, names and demonyms are matched after `_key` (accents stripped,
# casefolded, punctuation -> space, "St" -> "saint", leading "the" dropped).
_TABLE: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    # --- Africa ---
    ("Algeria", "DZ", "DZA", ("People's Democratic Republic of Algeria",), ("Algerian",)),
    ("Angola", "AO", "AGO", ("Republic of Angola",), ("Angolan",)),
    ("Benin", "BJ", "BEN", ("Republic of Benin", "Dahomey"), ("Beninese", "Beninois")),
    ("Botswana", "BW", "BWA", ("Republic of Botswana",), ("Motswana", "Batswana", "Botswanan")),
    ("Burkina Faso", "BF", "BFA", ("Upper Volta",), ("Burkinabe", "Burkinabé")),
    ("Burundi", "BI", "BDI", ("Republic of Burundi",), ("Burundian",)),
    ("Cameroon", "CM", "CMR", ("Republic of Cameroon", "Cameroun"), ("Cameroonian",)),
    ("Cape Verde", "CV", "CPV", ("Cabo Verde", "Republic of Cabo Verde"), ("Cape Verdean", "Cabo Verdean")),
    ("Central African Republic", "CF", "CAF", (), ("Central African",)),
    ("Chad", "TD", "TCD", ("Republic of Chad", "Tchad"), ("Chadian",)),
    ("Comoros", "KM", "COM", ("Union of the Comoros",), ("Comorian", "Comoran")),
    ("Republic of the Congo", "CG", "COG", ("Congo-Brazzaville", "Congo Brazzaville", "Congo, Republic of",
                                            "Congo, Republic of the", "Congo (Brazzaville)"), ()),
    ("Democratic Republic of the Congo", "CD", "COD", ("DR Congo", "DRC", "D.R. Congo", "Congo-Kinshasa",
                                                       "Congo Kinshasa", "Congo (Kinshasa)", "Zaire",
                                                       "Congo, Democratic Republic of",
                                                       "Congo, Democratic Republic of the",
                                                       "Congo, The Democratic Republic of the",
                                                       "Democratic Republic of Congo"), ()),
    ("Côte d'Ivoire", "CI", "CIV", ("Cote D'Ivoire", "Cote d Ivoire", "Cote dIvoire", "Ivory Coast",
                                    "Republic of Cote d'Ivoire"), ("Ivorian", "Ivoirian")),
    ("Djibouti", "DJ", "DJI", ("Republic of Djibouti",), ("Djiboutian",)),
    ("Egypt", "EG", "EGY", ("Arab Republic of Egypt",), ("Egyptian",)),
    ("Equatorial Guinea", "GQ", "GNQ", ("Republic of Equatorial Guinea",),
     ("Equatorial Guinean", "Equatoguinean")),
    ("Eritrea", "ER", "ERI", ("State of Eritrea",), ("Eritrean",)),
    ("Eswatini", "SZ", "SWZ", ("Swaziland", "Kingdom of Eswatini"), ("Swazi", "Liswati")),
    ("Ethiopia", "ET", "ETH", ("Federal Democratic Republic of Ethiopia",), ("Ethiopian",)),
    ("Gabon", "GA", "GAB", ("Gabonese Republic",), ("Gabonese",)),
    ("Gambia", "GM", "GMB", ("The Gambia", "Republic of the Gambia", "Gambia, The"), ("Gambian",)),
    ("Ghana", "GH", "GHA", ("Republic of Ghana",), ("Ghanaian", "Ghanian")),
    ("Guinea", "GN", "GIN", ("Republic of Guinea", "Guinea-Conakry", "Guinea Conakry"), ("Guinean",)),
    ("Guinea-Bissau", "GW", "GNB", ("Guinea Bissau", "Republic of Guinea-Bissau"), ("Bissau-Guinean",)),
    ("Kenya", "KE", "KEN", ("Republic of Kenya",), ("Kenyan",)),
    ("Lesotho", "LS", "LSO", ("Kingdom of Lesotho",), ("Mosotho", "Basotho")),
    ("Liberia", "LR", "LBR", ("Republic of Liberia",), ("Liberian",)),
    ("Libya", "LY", "LBY", ("State of Libya", "Libyan Arab Jamahiriya"), ("Libyan",)),
    ("Madagascar", "MG", "MDG", ("Republic of Madagascar",), ("Malagasy", "Madagascan")),
    ("Malawi", "MW", "MWI", ("Republic of Malawi",), ("Malawian",)),
    ("Mali", "ML", "MLI", ("Republic of Mali",), ("Malian",)),
    ("Mauritania", "MR", "MRT", ("Islamic Republic of Mauritania",), ("Mauritanian",)),
    ("Mauritius", "MU", "MUS", ("Republic of Mauritius",), ("Mauritian",)),
    ("Morocco", "MA", "MAR", ("Kingdom of Morocco",), ("Moroccan",)),
    ("Mozambique", "MZ", "MOZ", ("Republic of Mozambique",), ("Mozambican",)),
    ("Namibia", "NA", "NAM", ("Republic of Namibia",), ("Namibian",)),
    ("Niger", "NE", "NER", ("Republic of the Niger", "Republic of Niger"), ("Nigerien",)),
    ("Nigeria", "NG", "NGA", ("Federal Republic of Nigeria",), ("Nigerian",)),
    ("Rwanda", "RW", "RWA", ("Republic of Rwanda",), ("Rwandan", "Rwandese")),
    ("Sao Tome and Principe", "ST", "STP", ("São Tomé and Príncipe", "Sao Tome & Principe", "Sao Tome"),
     ("Sao Tomean", "Santomean")),
    ("Senegal", "SN", "SEN", ("Republic of Senegal",), ("Senegalese",)),
    ("Seychelles", "SC", "SYC", ("Republic of Seychelles",), ("Seychellois",)),
    ("Sierra Leone", "SL", "SLE", ("Republic of Sierra Leone",), ("Sierra Leonean",)),
    ("Somalia", "SO", "SOM", ("Federal Republic of Somalia",), ("Somali", "Somalian")),
    ("South Africa", "ZA", "ZAF", ("Republic of South Africa", "RSA"), ("South African",)),
    ("South Sudan", "SS", "SSD", ("Republic of South Sudan",), ("South Sudanese",)),
    ("Sudan", "SD", "SDN", ("Republic of the Sudan", "Republic of Sudan", "The Sudan"), ("Sudanese",)),
    ("Tanzania", "TZ", "TZA", ("United Republic of Tanzania", "Tanzania, United Republic of"),
     ("Tanzanian",)),
    ("Togo", "TG", "TGO", ("Togolese Republic",), ("Togolese",)),
    ("Tunisia", "TN", "TUN", ("Republic of Tunisia",), ("Tunisian",)),
    ("Uganda", "UG", "UGA", ("Republic of Uganda",), ("Ugandan",)),
    ("Western Sahara", "EH", "ESH", ("Sahrawi Arab Democratic Republic",), ("Sahrawi", "Sahrawian")),
    ("Zambia", "ZM", "ZMB", ("Republic of Zambia",), ("Zambian",)),
    ("Zimbabwe", "ZW", "ZWE", ("Republic of Zimbabwe",), ("Zimbabwean",)),
    # --- Americas ---
    ("Antigua and Barbuda", "AG", "ATG", ("Antigua & Barbuda", "Antigua"), ("Antiguan", "Barbudan")),
    ("Argentina", "AR", "ARG", ("Argentine Republic",), ("Argentine", "Argentinian", "Argentinean")),
    ("Bahamas", "BS", "BHS", ("The Bahamas", "Bahamas, The", "Commonwealth of The Bahamas"), ("Bahamian",)),
    ("Barbados", "BB", "BRB", (), ("Barbadian", "Bajan")),
    ("Belize", "BZ", "BLZ", (), ("Belizean",)),
    ("Bolivia", "BO", "BOL", ("Plurinational State of Bolivia", "Bolivia, Plurinational State of",
                              "Bolivia (Plurinational State of)"), ("Bolivian",)),
    ("Brazil", "BR", "BRA", ("Brasil", "Federative Republic of Brazil"), ("Brazilian",)),
    ("Canada", "CA", "CAN", (), ("Canadian",)),
    ("Chile", "CL", "CHL", ("Republic of Chile",), ("Chilean",)),
    ("Colombia", "CO", "COL", ("Republic of Colombia",), ("Colombian",)),
    ("Costa Rica", "CR", "CRI", ("Republic of Costa Rica",), ("Costa Rican",)),
    ("Cuba", "CU", "CUB", ("Republic of Cuba",), ("Cuban",)),
    ("Dominica", "DM", "DMA", ("Commonwealth of Dominica",), ()),
    ("Dominican Republic", "DO", "DOM", ("Republica Dominicana",), ()),
    ("Ecuador", "EC", "ECU", ("Republic of Ecuador",), ("Ecuadorian", "Ecuadorean")),
    ("El Salvador", "SV", "SLV", ("Republic of El Salvador",), ("Salvadoran", "Salvadorean")),
    ("Grenada", "GD", "GRD", (), ("Grenadian",)),
    ("Guatemala", "GT", "GTM", ("Republic of Guatemala",), ("Guatemalan",)),
    ("Guyana", "GY", "GUY", ("Co-operative Republic of Guyana",), ("Guyanese",)),
    ("Haiti", "HT", "HTI", ("Republic of Haiti",), ("Haitian",)),
    ("Honduras", "HN", "HND", ("Republic of Honduras",), ("Honduran",)),
    ("Jamaica", "JM", "JAM", (), ("Jamaican",)),
    ("Mexico", "MX", "MEX", ("United Mexican States", "Mexican Republic"), ("Mexican",)),
    ("Nicaragua", "NI", "NIC", ("Republic of Nicaragua",), ("Nicaraguan",)),
    ("Panama", "PA", "PAN", ("Republic of Panama",), ("Panamanian",)),
    ("Paraguay", "PY", "PRY", ("Republic of Paraguay",), ("Paraguayan",)),
    ("Peru", "PE", "PER", ("Republic of Peru",), ("Peruvian",)),
    ("Saint Kitts and Nevis", "KN", "KNA", ("St Kitts and Nevis", "Saint Kitts & Nevis", "St Kitts", "Saint Kitts",
                                            "Saint Christopher and Nevis"), ("Kittitian", "Nevisian")),
    ("Saint Lucia", "LC", "LCA", ("St Lucia",), ("Saint Lucian", "St Lucian")),
    ("Saint Vincent and the Grenadines", "VC", "VCT", ("St Vincent and the Grenadines", "Saint Vincent",
                                                       "St Vincent", "Saint Vincent & the Grenadines"),
     ("Vincentian",)),
    ("Suriname", "SR", "SUR", ("Surinam", "Republic of Suriname"), ("Surinamese", "Surinamer")),
    ("Trinidad and Tobago", "TT", "TTO", ("Trinidad & Tobago", "Trinidad", "Republic of Trinidad and Tobago"),
     ("Trinidadian", "Tobagonian", "Trinbagonian")),
    ("United States", "US", "USA", ("United States of America", "U.S.", "U.S.A.", "America",
                                    "United States (USA)"), ("American",)),
    ("Uruguay", "UY", "URY", ("Oriental Republic of Uruguay",), ("Uruguayan",)),
    ("Venezuela", "VE", "VEN", ("Bolivarian Republic of Venezuela", "Venezuela, Bolivarian Republic of",
                                "Venezuela (Bolivarian Republic of)"), ("Venezuelan",)),
    # --- Asia ---
    ("Afghanistan", "AF", "AFG", ("Islamic Republic of Afghanistan", "Islamic Emirate of Afghanistan"),
     ("Afghan", "Afghani")),
    ("Bahrain", "BH", "BHR", ("Kingdom of Bahrain",), ("Bahraini",)),
    ("Bangladesh", "BD", "BGD", ("People's Republic of Bangladesh",), ("Bangladeshi",)),
    ("Bhutan", "BT", "BTN", ("Kingdom of Bhutan",), ("Bhutanese",)),
    ("Brunei", "BN", "BRN", ("Brunei Darussalam", "Nation of Brunei"), ("Bruneian",)),
    ("Cambodia", "KH", "KHM", ("Kingdom of Cambodia", "Kampuchea"), ("Cambodian", "Khmer")),
    ("China", "CN", "CHN", ("People's Republic of China", "PRC", "P.R. China", "PR China", "Mainland China"),
     ("Chinese",)),
    ("Hong Kong", "HK", "HKG", ("Hong Kong SAR", "Hong Kong SAR China", "Hong Kong, China",
                                "Hong Kong Special Administrative Region",
                                "Hong Kong Special Administrative Region of China"),
     ("Hongkonger", "Hong Konger", "Hong Kongese")),
    ("Macao", "MO", "MAC", ("Macau", "Macao SAR", "Macau SAR", "Macao SAR China", "Macao, China",
                            "Macao Special Administrative Region",
                            "Macao Special Administrative Region of China",
                            "Macau Special Administrative Region of China"), ("Macanese",)),
    ("India", "IN", "IND", ("Republic of India", "Bharat"), ("Indian",)),
    ("Indonesia", "ID", "IDN", ("Republic of Indonesia",), ("Indonesian",)),
    ("Iran", "IR", "IRN", ("Islamic Republic of Iran", "Iran, Islamic Republic of",
                           "Iran (Islamic Republic of)", "Persia"), ("Iranian",)),
    ("Iraq", "IQ", "IRQ", ("Republic of Iraq",), ("Iraqi",)),
    ("Israel", "IL", "ISR", ("State of Israel",), ("Israeli",)),
    ("Japan", "JP", "JPN", ("Nippon",), ("Japanese",)),
    ("Jordan", "JO", "JOR", ("Hashemite Kingdom of Jordan",), ("Jordanian",)),
    ("Kazakhstan", "KZ", "KAZ", ("Republic of Kazakhstan",), ("Kazakh", "Kazakhstani")),
    ("Kuwait", "KW", "KWT", ("State of Kuwait",), ("Kuwaiti",)),
    ("Kyrgyzstan", "KG", "KGZ", ("Kyrgyz Republic", "Kirghizia"), ("Kyrgyz", "Kyrgyzstani", "Kirghiz")),
    ("Laos", "LA", "LAO", ("Lao PDR", "Lao People's Democratic Republic", "Lao People's Democratic Rep"),
     ("Laotian", "Lao")),
    ("Lebanon", "LB", "LBN", ("Lebanese Republic",), ("Lebanese",)),
    ("Malaysia", "MY", "MYS", (), ("Malaysian",)),
    ("Maldives", "MV", "MDV", ("Republic of Maldives",), ("Maldivian",)),
    ("Mongolia", "MN", "MNG", (), ("Mongolian",)),
    ("Myanmar", "MM", "MMR", ("Burma", "Republic of the Union of Myanmar"), ("Burmese", "Myanmarese")),
    ("Nepal", "NP", "NPL", ("Federal Democratic Republic of Nepal",), ("Nepalese", "Nepali")),
    ("North Korea", "KP", "PRK", ("Democratic People's Republic of Korea", "DPRK", "D.P.R.K.",
                                  "Korea, Democratic People's Republic of", "Korea DPR", "Korea, North",
                                  "Korea (North)", "Korea (Democratic People's Republic of)"),
     ("North Korean",)),
    ("Oman", "OM", "OMN", ("Sultanate of Oman",), ("Omani",)),
    ("Pakistan", "PK", "PAK", ("Islamic Republic of Pakistan",), ("Pakistani",)),
    ("Palestine", "PS", "PSE", ("Palestina", "State of Palestine", "Palestine, State of",
                                "Palestinian Territories", "Palestinian Territory",
                                "Occupied Palestinian Territory", "West Bank and Gaza", "Gaza Strip",
                                "West Bank", "Gaza"), ("Palestinian",)),
    ("Philippines", "PH", "PHL", ("The Philippines", "Republic of the Philippines"),
     ("Filipino", "Filipina", "Philippine")),
    ("Qatar", "QA", "QAT", ("State of Qatar",), ("Qatari",)),
    ("Saudi Arabia", "SA", "SAU", ("Kingdom of Saudi Arabia", "KSA"), ("Saudi", "Saudi Arabian")),
    ("Singapore", "SG", "SGP", ("Republic of Singapore",), ("Singaporean",)),
    ("South Korea", "KR", "KOR", ("Republic of Korea", "Korea, Republic of", "Korea Republic", "Korea, South",
                                  "Korea (South)", "Korea (Republic of)", "ROK"), ("South Korean",)),
    ("Sri Lanka", "LK", "LKA", ("Ceylon", "Democratic Socialist Republic of Sri Lanka"), ("Sri Lankan",)),
    ("Syria", "SY", "SYR", ("Syrian Arab Republic",), ("Syrian",)),
    ("Taiwan", "TW", "TWN", ("Chinese Taipei", "Taiwan, Province of China", "Taiwan (Republic of China)"),
     ("Taiwanese",)),
    ("Tajikistan", "TJ", "TJK", ("Republic of Tajikistan",), ("Tajik", "Tajikistani")),
    ("Thailand", "TH", "THA", ("Kingdom of Thailand", "Siam"), ("Thai",)),
    ("Timor-Leste", "TL", "TLS", ("East Timor", "Timor Leste", "Democratic Republic of Timor-Leste"),
     ("Timorese", "East Timorese")),
    ("Turkmenistan", "TM", "TKM", (), ("Turkmen", "Turkmenistani")),
    ("United Arab Emirates", "AE", "ARE", ("UAE", "U.A.E.", "Emirates", "The Emirates"), ("Emirati",)),
    ("Uzbekistan", "UZ", "UZB", ("Republic of Uzbekistan",), ("Uzbek", "Uzbekistani")),
    ("Vietnam", "VN", "VNM", ("Viet Nam", "Socialist Republic of Vietnam", "Socialist Republic of Viet Nam"),
     ("Vietnamese",)),
    ("Yemen", "YE", "YEM", ("Republic of Yemen",), ("Yemeni", "Yemenite")),
    # --- Europe ---
    ("Albania", "AL", "ALB", ("Republic of Albania",), ("Albanian",)),
    ("Andorra", "AD", "AND", ("Principality of Andorra",), ("Andorran",)),
    ("Armenia", "AM", "ARM", ("Republic of Armenia",), ("Armenian",)),
    ("Austria", "AT", "AUT", ("Republic of Austria",), ("Austrian",)),
    ("Azerbaijan", "AZ", "AZE", ("Republic of Azerbaijan",), ("Azerbaijani", "Azeri")),
    ("Belarus", "BY", "BLR", ("Republic of Belarus", "Byelorussia"), ("Belarusian", "Belarussian")),
    ("Belgium", "BE", "BEL", ("Kingdom of Belgium",), ("Belgian",)),
    ("Bosnia and Herzegovina", "BA", "BIH", ("Bosnia & Herzegovina", "Bosnia-Herzegovina", "Bosnia",
                                             "Bosnia Herzegovina"), ("Bosnian", "Herzegovinian")),
    ("Bulgaria", "BG", "BGR", ("Republic of Bulgaria",), ("Bulgarian",)),
    ("Croatia", "HR", "HRV", ("Republic of Croatia", "Hrvatska"), ("Croatian", "Croat")),
    ("Cyprus", "CY", "CYP", ("Republic of Cyprus",), ("Cypriot",)),
    ("Czech Republic", "CZ", "CZE", ("Czechia", "Czech Rep"), ("Czech",)),
    ("Denmark", "DK", "DNK", ("Kingdom of Denmark",), ("Danish", "Dane")),
    ("Estonia", "EE", "EST", ("Republic of Estonia",), ("Estonian",)),
    ("Finland", "FI", "FIN", ("Republic of Finland",), ("Finnish", "Finn")),
    ("France", "FR", "FRA", ("French Republic",), ("French",)),
    ("Georgia", "GE", "GEO", ("Sakartvelo",), ("Georgian",)),
    ("Germany", "DE", "DEU", ("Federal Republic of Germany", "Deutschland"), ("German",)),
    ("Greece", "GR", "GRC", ("Hellenic Republic", "Hellas"), ("Greek", "Hellenic")),
    ("Hungary", "HU", "HUN", (), ("Hungarian",)),
    ("Iceland", "IS", "ISL", (), ("Icelandic", "Icelander")),
    ("Ireland", "IE", "IRL", ("Republic of Ireland", "Eire", "Éire"), ("Irish",)),
    ("Italy", "IT", "ITA", ("Italian Republic", "Italia"), ("Italian",)),
    ("Kosovo", "XK", "XKX", ("Republic of Kosovo",), ("Kosovar", "Kosovan")),
    ("Latvia", "LV", "LVA", ("Republic of Latvia",), ("Latvian",)),
    ("Liechtenstein", "LI", "LIE", ("Principality of Liechtenstein",), ("Liechtensteiner",)),
    ("Lithuania", "LT", "LTU", ("Republic of Lithuania",), ("Lithuanian",)),
    ("Luxembourg", "LU", "LUX", ("Grand Duchy of Luxembourg",), ("Luxembourgish", "Luxembourger")),
    ("Malta", "MT", "MLT", ("Republic of Malta",), ("Maltese",)),
    ("Moldova", "MD", "MDA", ("Republic of Moldova", "Moldova, Republic of"), ("Moldovan",)),
    ("Monaco", "MC", "MCO", ("Principality of Monaco",), ("Monegasque", "Monacan")),
    ("Montenegro", "ME", "MNE", (), ("Montenegrin",)),
    ("Netherlands", "NL", "NLD", ("The Netherlands", "Holland", "Kingdom of the Netherlands",
                                  "Netherlands, The"), ("Dutch", "Netherlander")),
    ("North Macedonia", "MK", "MKD", ("Macedonia", "Republic of North Macedonia", "Republic of Macedonia",
                                      "FYROM", "Former Yugoslav Republic of Macedonia",
                                      "Macedonia, the former Yugoslav Republic of"),
     ("Macedonian", "North Macedonian")),
    ("Norway", "NO", "NOR", ("Kingdom of Norway",), ("Norwegian",)),
    ("Poland", "PL", "POL", ("Republic of Poland", "Polska"), ("Polish", "Pole")),
    ("Portugal", "PT", "PRT", ("Portuguese Republic",), ("Portuguese",)),
    ("Romania", "RO", "ROU", ("Roumania", "Rumania"), ("Romanian",)),
    ("Russia", "RU", "RUS", ("Russian Federation",), ("Russian",)),
    ("San Marino", "SM", "SMR", ("Republic of San Marino",), ("Sammarinese",)),
    ("Serbia", "RS", "SRB", ("Republic of Serbia",), ("Serbian", "Serb")),
    ("Slovakia", "SK", "SVK", ("Slovak Republic",), ("Slovak", "Slovakian")),
    ("Slovenia", "SI", "SVN", ("Republic of Slovenia",), ("Slovenian", "Slovene")),
    ("Spain", "ES", "ESP", ("Kingdom of Spain", "Espana"), ("Spanish", "Spaniard")),
    ("Sweden", "SE", "SWE", ("Kingdom of Sweden",), ("Swedish", "Swede")),
    ("Switzerland", "CH", "CHE", ("Swiss Confederation",), ("Swiss",)),
    ("Turkey", "TR", "TUR", ("Türkiye", "Turkiye", "Republic of Turkey", "Republic of Türkiye"),
     ("Turkish", "Turk")),
    ("Ukraine", "UA", "UKR", (), ("Ukrainian",)),
    ("United Kingdom", "GB", "GBR", ("UK", "U.K.", "Great Britain", "Britain",
                                     "United Kingdom of Great Britain and Northern Ireland",
                                     "England", "Scotland", "Wales", "Northern Ireland"),
     ("British", "Briton", "English", "Scottish", "Scots", "Welsh", "Northern Irish")),
    ("Vatican City", "VA", "VAT", ("Holy See", "Holy See (Vatican City State)", "Vatican City State",
                                   "Vatican"), ()),
    # --- Oceania ---
    ("Australia", "AU", "AUS", ("Commonwealth of Australia",), ("Australian", "Aussie")),
    ("Fiji", "FJ", "FJI", ("Republic of Fiji",), ("Fijian",)),
    ("Kiribati", "KI", "KIR", (), ("I-Kiribati",)),
    ("Marshall Islands", "MH", "MHL", ("Republic of the Marshall Islands",), ("Marshallese",)),
    ("Micronesia", "FM", "FSM", ("Federated States of Micronesia", "Micronesia, Federated States of",
                                 "Micronesia (Federated States of)"), ("Micronesian",)),
    ("Nauru", "NR", "NRU", ("Republic of Nauru",), ("Nauruan",)),
    ("New Zealand", "NZ", "NZL", ("Aotearoa",), ("New Zealander",)),
    ("Palau", "PW", "PLW", ("Republic of Palau",), ("Palauan",)),
    ("Papua New Guinea", "PG", "PNG", (), ("Papua New Guinean",)),
    ("Samoa", "WS", "WSM", ("Independent State of Samoa", "Western Samoa"), ("Samoan",)),
    ("Solomon Islands", "SB", "SLB", (), ("Solomon Islander",)),
    ("Tonga", "TO", "TON", ("Kingdom of Tonga",), ("Tongan",)),
    ("Tuvalu", "TV", "TUV", (), ("Tuvaluan",)),
    ("Vanuatu", "VU", "VUT", ("Republic of Vanuatu",), ("Ni-Vanuatu", "Vanuatuan")),
    # --- Territories and other ISO 3166 entries (each its own country, SOP P6) ---
    ("Åland Islands", "AX", "ALA", ("Aland Islands", "Aland"), ()),
    ("American Samoa", "AS", "ASM", (), ("American Samoan",)),
    ("Anguilla", "AI", "AIA", (), ("Anguillian",)),
    ("Antarctica", "AQ", "ATA", (), ()),
    ("Aruba", "AW", "ABW", (), ("Aruban",)),
    ("Bermuda", "BM", "BMU", (), ("Bermudian", "Bermudan")),
    ("Bouvet Island", "BV", "BVT", (), ()),
    ("British Indian Ocean Territory", "IO", "IOT", ("Chagos Islands",), ()),
    ("British Virgin Islands", "VG", "VGB", ("Virgin Islands, British", "Virgin Islands (British)", "BVI"), ()),
    ("Caribbean Netherlands", "BQ", "BES", ("Bonaire", "Bonaire, Sint Eustatius and Saba",
                                            "Bonaire, Saint Eustatius and Saba", "Sint Eustatius", "Saba"), ()),
    ("Cayman Islands", "KY", "CYM", ("Caymans", "Cayman"), ("Caymanian",)),
    ("Christmas Island", "CX", "CXR", (), ()),
    ("Cocos (Keeling) Islands", "CC", "CCK", ("Cocos Islands", "Keeling Islands"), ()),
    ("Cook Islands", "CK", "COK", (), ("Cook Islander",)),
    ("Curaçao", "CW", "CUW", ("Curacao",), ("Curaçaoan", "Curacaoan")),
    ("Falkland Islands", "FK", "FLK", ("Falkland Islands (Malvinas)", "Falklands", "Malvinas"),
     ("Falkland Islander",)),
    ("Faroe Islands", "FO", "FRO", ("Faroes", "Faeroe Islands"), ("Faroese",)),
    ("French Guiana", "GF", "GUF", ("Guyane",), ("French Guianese",)),
    ("French Polynesia", "PF", "PYF", ("Tahiti",), ("French Polynesian", "Tahitian")),
    ("French Southern Territories", "TF", "ATF", ("French Southern and Antarctic Lands",), ()),
    ("Gibraltar", "GI", "GIB", (), ("Gibraltarian",)),
    ("Greenland", "GL", "GRL", ("Kalaallit Nunaat",), ("Greenlandic", "Greenlander")),
    ("Guadeloupe", "GP", "GLP", (), ("Guadeloupean",)),
    ("Guam", "GU", "GUM", (), ("Guamanian",)),
    ("Guernsey", "GG", "GGY", ("Bailiwick of Guernsey",), ()),
    ("Heard Island and McDonald Islands", "HM", "HMD", ("Heard Island",), ()),
    ("Isle of Man", "IM", "IMN", ("Isle Of Man",), ("Manx",)),
    ("Jersey", "JE", "JEY", ("Bailiwick of Jersey",), ()),
    ("Martinique", "MQ", "MTQ", (), ("Martiniquais",)),
    ("Mayotte", "YT", "MYT", (), ("Mahoran",)),
    ("Montserrat", "MS", "MSR", (), ("Montserratian",)),
    ("New Caledonia", "NC", "NCL", (), ("New Caledonian",)),
    ("Niue", "NU", "NIU", (), ("Niuean",)),
    ("Norfolk Island", "NF", "NFK", (), ()),
    ("Northern Mariana Islands", "MP", "MNP", ("Commonwealth of the Northern Mariana Islands",
                                               "Northern Marianas", "Saipan"), ()),
    ("Pitcairn Islands", "PN", "PCN", ("Pitcairn",), ("Pitcairn Islander",)),
    ("Puerto Rico", "PR", "PRI", ("Commonwealth of Puerto Rico",), ("Puerto Rican",)),
    ("Réunion", "RE", "REU", ("Reunion",), ("Reunionese",)),
    ("Saint Barthélemy", "BL", "BLM", ("Saint Barthelemy", "St Barts", "St Barths", "Saint Barts"), ()),
    ("Saint Helena", "SH", "SHN", ("Saint Helena, Ascension and Tristan da Cunha",
                                   "St Helena", "Ascension Island", "Tristan da Cunha"), ("Saint Helenian",)),
    ("Saint Martin", "MF", "MAF", ("Saint Martin (French part)", "St Martin", "Saint-Martin"), ()),
    ("Saint Pierre and Miquelon", "PM", "SPM", ("St Pierre and Miquelon",), ()),
    ("Sint Maarten", "SX", "SXM", ("Sint Maarten (Dutch part)",), ()),
    ("South Georgia and the South Sandwich Islands", "GS", "SGS", ("South Georgia",
                                                                   "South Sandwich Islands"), ()),
    ("Svalbard and Jan Mayen", "SJ", "SJM", ("Svalbard", "Jan Mayen"), ()),
    ("Tokelau", "TK", "TKL", (), ("Tokelauan",)),
    ("Turks and Caicos Islands", "TC", "TCA", ("Turks & Caicos Islands", "Turks and Caicos"), ()),
    ("United States Minor Outlying Islands", "UM", "UMI", ("US Minor Outlying Islands",), ()),
    ("United States Virgin Islands", "VI", "VIR", ("US Virgin Islands", "U.S. Virgin Islands",
                                                   "Virgin Islands, U.S.", "Virgin Islands (U.S.)",
                                                   "Virgin Islands, US", "USVI"), ()),
    ("Wallis and Futuna", "WF", "WLF", ("Wallis & Futuna",), ()),
)

# Phrases that sound like one country but are not (or are a part of another one).
# Mapped to a country when the phrase pins it down (a US state, a Canadian province),
# else to None. They also stop shorter matches in free text ("Latin American" must
# not produce United States, "New Guinea" must not produce Guinea).
_SUBNATIONAL: dict[str, str] = {
    "New Jersey": "United States",
    "New Mexico": "United States",
    "British Columbia": "Canada",
    "New South Wales": "Australia",
    "Jersey City": "United States",
    "Niger Delta": "Nigeria",
    "Niger State": "Nigeria",
}

# Not a country: regions, groupings, placeholders (SOP P5: "International", "Europe").
_NOT_COUNTRY_PHRASES: tuple[str, ...] = (
    "International", "Europe", "European Union", "EU", "Global", "Worldwide", "World", "Unknown",
    "N/A", "NA/NA", "None", "Null", "Nil", "Not Known", "Not Applicable", "Not Available", "Not Specified",
    "Unspecified", "Stateless", "Various", "Multiple", "Other", "Offshore", "XX", "ZZ", "-", "?",
    "Africa", "Asia", "Oceania", "Middle East", "Near East", "Far East", "Latin America", "South America",
    "North America", "Central America", "Americas", "Caribbean", "West Indies", "Scandinavia", "Nordics",
    "Balkans", "Baltics", "Gulf", "Persian Gulf", "Arabian Gulf", "GCC", "MENA", "EMEA", "APAC",
    "Asia Pacific", "Asia-Pacific", "West Africa", "East Africa", "North Africa", "Southern Africa",
    "Central Africa", "Sub-Saharan Africa", "Central Asia", "South Asia", "Southeast Asia", "East Asia",
    "Eastern Europe", "Western Europe", "Central Europe", "Northern Europe", "Southern Europe",
    "Indian Ocean", "Pacific", "Pacific Islands", "Arab World", "Gulf of Guinea",
    # adjectives of regions: stop "American", "Indian" etc. inside them in free text
    "Latin American", "South American", "North American", "Central American", "Pan American",
    "Pan-American", "West Indian", "West African", "East African", "North African", "Southern African",
    "European", "Asian", "African", "Middle Eastern", "Arab", "Scandinavian", "Caribbean",
)

# More than one country fits: never guess (SOP safety). Treated as unknown.
_AMBIGUOUS_PHRASES: tuple[str, ...] = (
    "Congo", "Congolese", "Korea", "Korean", "Virgin Islands", "Virgin Islander", "Dominican",
    "Guinea Coast", "New Guinea", "Republic of China", "Yugoslavia", "Yugoslav", "Yugoslavian",
    "Czechoslovakia", "Czechoslovak", "USSR", "U.S.S.R.", "Soviet Union", "Soviet", "Serbia and Montenegro",
    "Netherlands Antilles", "Antilles", "East Germany", "West Germany", "Rhodesia",
    "Macedonia, Greece", "NA",
)

# Free-text scanning: these aliases are too common as ordinary words (a language,
# a surname, a noun) to read as a country inside a sentence. Structured fields still
# map them.
_TEXT_SKIP = frozenset({
    "english", "america", "pole", "finn", "dane", "swede", "turk", "lao", "khmer", "scots", "kiwi",
    "aussie", "hellenic", "salvador", "trinidad", "antigua", "bosnia", "cayman", "saba", "gaza",
    "west bank", "emirates", "the emirates", "persia", "siam", "bharat", "nippon", "italia", "polska",
    "espana", "brasil", "deutschland", "hellas", "rsa", "ksa", "rok", "bvi", "usvi", "fyrom",
    "vatican", "central african", "sao tome", "saint vincent", "saint kitts",
    "saint martin", "aland", "tahiti", "saipan", "georgian", "afghani",
})

# Free-text scanning: only when written with a capital letter (lowercase means the
# ordinary word: turkey, china, jersey, polish, chile, japan, panama hat ...).
_TEXT_CAPITALISED = frozenset({
    "turkey", "china", "jersey", "polish", "guinea", "chile", "japan", "panama", "french", "swiss",
    "danish", "dutch", "chad", "jordan", "georgia", "india", "bermuda", "brazil", "canada", "manx",
})

# Free-text scanning: also common given names. Skipped when a capitalised word sits
# right next to them ("Jordan Andrew Chartrand", "Michael Jordan"), kept in "in Jordan,".
_TEXT_GIVEN_NAMES = frozenset({"jordan", "chad", "israel", "georgia", "india", "kenya"})
# Capitalised words that are not part of a name ("In Jordan, ...", "From Chad").
_FUNCTION_WORDS = frozenset({
    "in", "within", "from", "of", "to", "across", "the", "and", "for", "at", "by", "near", "outside",
    "inside", "throughout", "into", "via", "or", "with", "both", "a", "an",
})

# Abbreviations matched in free text only when written in capitals ("US", "U.S.", "UAE").
_TEXT_ABBREVIATIONS = frozenset({
    "uk", "u k", "usa", "u s", "u s a", "us", "uae", "u a e", "dprk", "d p r k", "drc", "d r congo",
    "prc", "p r china", "pr china", "korea dpr",
})

# In "<place>, <last part>" the last part alone is not trusted for these (a US state).
_UNRELIABLE_LAST_PART = frozenset({"georgia"})

_BRACKETS = re.compile(r"\[[^\]]*\]")
_NON_WORD = re.compile(r"[\W_]+")


def _key(raw: str) -> str:
    """Lookup key: accents stripped, casefolded, brackets and punctuation removed,
    "St" -> "saint", a leading "the" dropped."""
    text = _BRACKETS.sub(" ", strip_accents(raw)).replace("&", " and ").casefold()
    words = _NON_WORD.sub(" ", text).split()
    words = ["saint" if w == "st" else w for w in words]
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return " ".join(words)


_COUNTRY = "country"
_NOT_COUNTRY = "not_country"
_AMBIGUOUS = "ambiguous"


def _build() -> tuple[dict[str, tuple[str, str | None]], dict[str, tuple[str, str | None]]]:
    """(structured lookup, free-text lookup): key -> (status, canonical | None).

    Raises ValueError if one key would mean two different things."""
    lookup: dict[str, tuple[str, str | None]] = {}
    text: dict[str, tuple[str, str | None]] = {}

    def add(raw: str, value: tuple[str, str | None], *, in_text: bool) -> None:
        key = _key(raw)
        if not key:
            return
        if lookup.get(key, value) != value:
            raise ValueError(f"country alias {raw!r} -> {value} clashes with {lookup[key]}")
        lookup[key] = value
        if in_text and key not in _TEXT_SKIP:
            text[key] = value

    for canonical, alpha2, alpha3, alternatives, demonyms in _TABLE:
        value = (_COUNTRY, canonical)
        for code in (alpha2, alpha3):
            if code != "NA":  # "NA" is far more often a placeholder than Namibia
                add(code, value, in_text=False)
        for name in (canonical, *alternatives, *demonyms):
            add(name, value, in_text=True)
    for phrase, canonical in _SUBNATIONAL.items():
        add(phrase, (_COUNTRY, canonical), in_text=True)
    for phrase in _NOT_COUNTRY_PHRASES:
        add(phrase, (_NOT_COUNTRY, None), in_text=True)
    for phrase in _AMBIGUOUS_PHRASES:
        add(phrase, (_AMBIGUOUS, None), in_text=True)
    # Abbreviations that are not codes still belong in text matching (capitals only).
    for abbreviation in _TEXT_ABBREVIATIONS:
        if abbreviation in lookup:
            text[abbreviation] = lookup[abbreviation]
    return lookup, text


_LOOKUP, _TEXT_LOOKUP = _build()

CANONICAL_COUNTRIES: frozenset[str] = frozenset(row[0] for row in _TABLE)
NOT_COUNTRIES: frozenset[str] = frozenset(k for k, (s, _) in _LOOKUP.items() if s == _NOT_COUNTRY)


def classify_country(raw: str | None) -> tuple[str | None, str]:
    """(canonical name | None, status) for one structured value.

    status: "country" (canonical given), "empty" (blank / missing), "not_country"
    ("International", "Europe", a region or a placeholder), "ambiguous" (could be
    more than one country), "unknown" (not in the table). Never guesses.

    Bracket tags are dropped ("United States [Associated]"). A value with commas
    that is not itself a country is read by its last part, as in an address
    ("Belgrade, Serbia" -> Serbia).
    """
    if raw is None:
        return None, "empty"
    key = _key(str(raw))
    if not key:
        return None, "empty"
    found = _LOOKUP.get(key)
    if found is None and "," in str(raw):
        last_raw = _BRACKETS.sub(" ", str(raw)).rstrip().rstrip(",").split(",")[-1]
        last = _key(last_raw)
        if last and last not in _UNRELIABLE_LAST_PART:
            found = _LOOKUP.get(last)
    if found is None:
        return None, "unknown"
    status, canonical = found
    return canonical, status


def normalize_country(raw: str | None) -> str | None:
    """Canonical country name, or None when blank, not a country, ambiguous or unknown."""
    return classify_country(raw)[0]


def _text_form(text: str) -> str:
    """Same length as `text`: accents stripped, casefolded, non-word chars -> space."""
    if text.isascii():
        return "".join(ch if ch.isalnum() else " " for ch in text.lower())
    out = []
    for ch in text:
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        if len(base) != 1:
            base = ch
        low = base.casefold()
        if len(low) != 1:
            low = base
        out.append(low if low.isalnum() else " ")
    return "".join(out)


def _text_index() -> tuple[dict[tuple[str, ...], tuple[str, tuple[str, str | None]]], int]:
    """Word-tuple -> (key, value) for free-text matching, plus the longest phrase length.

    Each phrase is also indexed without "and" ("Trinidad & Tobago" in prose)."""
    index: dict[tuple[str, ...], tuple[str, tuple[str, str | None]]] = {}
    for key in sorted(_TEXT_LOOKUP):
        words = tuple(key.split(" "))
        index.setdefault(words, (key, _TEXT_LOOKUP[key]))
    for key in sorted(_TEXT_LOOKUP):
        words = tuple(w for w in key.split(" ") if w != "and")
        if words:
            index.setdefault(words, (key, _TEXT_LOOKUP[key]))
    return index, max(len(words) for words in index)


_TEXT_INDEX, _TEXT_MAX_WORDS = _text_index()
_TOKEN = re.compile(r"[^\W_]+")


def _next_to_capitalised(original: str, tokens: list[tuple[int, int, str]], first: int, last: int) -> bool:
    """True if the word just before `first` or just after `last` is capitalised and
    separated only by spaces (part of a personal name)."""
    for other, gap in ((first - 1, (tokens[first - 1][1], tokens[first][0]) if first > 0 else None),
                       (last + 1, (tokens[last][1], tokens[last + 1][0]) if last + 1 < len(tokens) else None)):
        if gap is None:
            continue
        between = original[gap[0]:gap[1]]
        word = tokens[other][2]
        if between and not between.strip() and original[tokens[other][0]].isupper()                 and word not in _FUNCTION_WORDS:
            return True
    return False


def countries_in_text(text: str | None) -> tuple[str, ...]:
    """Countries named in free text (notes, search snippets, addresses), by name or
    demonym, in order of first appearance, each once.

    Word-boundary matching, longest phrase first ("Northern Ireland" is not Ireland,
    "Papua New Guinea" is not Guinea, "Latin American" is not the United States).
    ISO codes are not read from prose; "US", "UK", "UAE" only in capitals; words that
    are also ordinary English (turkey, china, jersey, polish) only when capitalised.
    Regions, "International" and ambiguous names ("Congo", "Korean") produce nothing.
    """
    if not text:
        return ()
    original = str(text)
    tokens = [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(_text_form(original))]
    words = ["saint" if w == "st" else w for _, _, w in tokens]
    found: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        step = 1
        for size in range(min(_TEXT_MAX_WORDS, n - i), 0, -1):
            entry = _TEXT_INDEX.get(tuple(words[i:i + size]))
            if entry is None:
                continue
            key, (status, canonical) = entry
            span = original[tokens[i][0]:tokens[i + size - 1][1]]
            if key in _TEXT_ABBREVIATIONS:
                if not all(ch.isupper() for ch in span if ch.isalpha()):
                    continue
            elif key in _TEXT_CAPITALISED and not span[:1].isupper():
                continue
            if key in _TEXT_GIVEN_NAMES and _next_to_capitalised(original, tokens, i, i + size - 1):
                continue
            if status == _COUNTRY and canonical and canonical not in found:
                found.append(canonical)
            step = size
            break
        i += step
    return tuple(found)


def find_countries(text: str | None) -> list[str]:
    """List form of `countries_in_text`."""
    return list(countries_in_text(text))
