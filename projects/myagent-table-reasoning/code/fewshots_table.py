""" 与 MACT（NAACL 2025）相关的工具类与函数。

版权 (c) 2025 Robert Bosch GmbH

本程序是自由软件：你可以按照自由软件基金会发布的 GNU Affero 通用公共许可证第 3 版或（你可以选择的）更高版本的条款重新发布和/或修改。

本程序的发布目的是希望它有用，但不提供任何保证；甚至不包含适销性或特定用途适用性的默示保证。详情参见 GNU Affero 通用公共许可证。

你应该已经收到一份 GNU Affero 通用公共许可证副本；如果没有，请访问 <https://www.gnu.org/licenses/>。
""" 

DEMO_WTQ_DIRECT = """
Table:
| Parish | Locality | Parish Priest | Founded | Closed |
| St Mary | Bacup | Fr Frank Thorpe | 1852 | ---- |
| Our Immaculate Mother & St Anselm | Whitworth | Fr Frank Thorpe | 1860 | ---- |
| St Joseph | Stacksteads | ---- | 1947 | 2005 |
| St Joseph & St Peter | Newchurch-In-Rossendale | Fr Philip Boast | 1915 | ---- |
| The Immaculate Conception | Haslingden | Fr Canon John Mackie | 1854 | ---- |
| St Veronica (Chapel of Ease) | Helmshore | Served from The Immaculate Conception | 1959 | ---- |
| St James the Less | Rawtenstall | Fr David Lupton, Rural Dean | 1828 | ---- |
Context:
Question: what's the number of parishes founded in the 1800s? 
Answer: Let's think step by step. I need to first find the parishes founded in the 1800s, they are: St Mary, Our Immaculate Mother & St Anselm, The Immaculate Conception and St James the Less. Since there are 4 parishes, therefore the answer is: 4.

Table:
| Election | Number of popular votes | % of popular votes | Total elected seats | +/− |
| 1988 | 139,982 | 22.16 | 61 / 264 | |
| 1991 | 170,757 | 32.11 | 83 / 272 | 22 |
| 1994 | 242,557 | 35.34 | 121 / 346 | 38 |
| 1999 | 271,251 | 33.45 | 122 / 390 | 1 |
| 2003 | 459,640 | 44.67 | 194 / 400 | 72 |
| 2007 | 445,781 | 39.15 | 127 / 405 | 30 |
| 2011 | 464,512 | 39.34 | 103 / 412 | 18 |
Context:
Question: what is the total number of popular votes cast in 2003?
Answer: Let's think step by step. I need to extract the total number of popular votes cast in 2003 from the table, the number is 459,640. Therefore, the answer is: 459,640.

Table:
| Year | Competition | Venue | Position | Notes |
| 1989 | European Indoor Championships | The Hague, Netherlands | 10th |  |
| 1989 | World Indoor Championships | Budapest, Hungary | 9th |  | 
| 1991 | World Indoor Championships | Lisbon, Portugal | 6th |  |
| 1991 | World Championships | Tokyo, Japan | 5th | 5.75 m NR |
| 1992 | European Indoor Championships | Genoa, Italy | 5th |  |
| 1993 | World Championships | Stuttgart, Germany | 11th |  |
Context:
Question: what peter widen's is the highest finish in all indoor championships?
Answer: Let's think step by step. I need to find the highest finish from the table, which is 5th. Therefore, the answer is: 5th.

[BREAK]
Dataframe code: import pandas as pd\ndata={'Parish':['St Mary', 'Our Immaculate Mother & St Anselm', 'St Joseph', 'St Joseph & St Peter', 'The Immaculate Conception', 'St Veronica (Chapel of Ease)', 'St James the Less'],'Locality':['Bacup', 'Whitworth', 'Stacksteads', 'Newchurch-In-Rossendale', 'Haslingden', 'Helmshore', 'Rawtenstall'],'Parish Priest':['Fr Frank Thorpe', 'Fr Frank Thorpe', '----', 'Fr Philip Boast', 'Fr Canon John Mackie', 'Served from The Immaculate Conception', 'Fr David Lupton, Rural Dean'],'Founded':[1852.0, 1860.0, 1947.0, 1915.0, 1854.0, 1959.0, 1828.0],'Closed':['----', '----', 2005.0, '----', '----', '----', '----']}\ndf=pd.DataFrame(data)
Context:
Question: what's the number of parishes founded in the 1800s? 
Code:  ```Python
parishes_founded_1800s = df[(df['Founded'] >= 1800) & (df['Founded'] < 1900)]  
result = len(parishes_founded_1800s)
```  

Dataframe code: import pandas as pd\ndata={'Election':[1988.0, 1991.0, 1994.0, 1999.0, 2003.0, 2007.0, 2011.0],'Number of popular votes':['139,982', '170,757', '242,557', '271,251', '459,640', '445,781', '464,512'],'% of popular votes':[22.16, 32.11, 35.34, 33.45, 44.67, 39.15, 39.34],'Total elected seats':['61 / 264', '83 / 272', '121 / 346', '122 / 390', '194 / 400', '127 / 405', '103 / 412'],'+/−':['', 22.0, 38.0, 1.0, 72.0, 30.0, 18.0]}\ndf=pd.DataFrame(data)
Context:
Question: what is the total number of popular votes cast in 2003? 
Code:  ```Python
result = df.loc[df['Election'] == 2003.0, 'Number of popular votes'].values[0]
```

Dataframe code: import pandas as pd\ndata={'Year':[1989.0, 1989.0, 1991.0, 1991.0, 1992.0, 1993.0],'Competition':['European Indoor Championships', 'World Indoor Championships', 'World Indoor Championships', 'World Championships', 'European Indoor Championships', 'World Championships'],'Venue':['The Hague, Netherlands', 'Budapest, Hungary', 'Lisbon, Portugal', 'Tokyo, Japan', 'Genoa, Italy', 'Stuttgart, Germany'],'Position':['10th', '9th', '6th', '5th', '5th', '11th'],'Notes':['', '', '', '5.75 m NR', '', '']}\ndf=pd.DataFrame(data)
Context:
Question: what peter widen's is the highest finish in all indoor championships?
Code:  ```Python
def position_to_int(position_str):    
    return int(''.join(filter(str.isdigit, position_str)))  

df['Position_Int'] = df['Position'].apply(position_to_int)  
indoor_championships = df[df['Competition'].str.contains('Indoor')]  
highest_finish_row = indoor_championships.loc[indoor_championships['Position_Int'].idxmin()]   
result = highest_finish_row['Position']    
```
"""

DEMO_WTQ = """Table:
| Parish | Locality | Parish Priest | Founded | Closed |
| St Mary | Bacup | Fr Frank Thorpe | 1852 | ---- |
| Our Immaculate Mother & St Anselm | Whitworth | Fr Frank Thorpe | 1860 | ---- |
| St Joseph | Stacksteads | ---- | 1947 | 2005 |
| St Joseph & St Peter | Newchurch-In-Rossendale | Fr Philip Boast | 1915 | ---- |
| The Immaculate Conception | Haslingden | Fr Canon John Mackie | 1854 | ---- |
| St Veronica (Chapel of Ease) | Helmshore | Served from The Immaculate Conception | 1959 | ---- |
| St James the Less | Rawtenstall | Fr David Lupton, Rural Dean | 1828 | ---- |
Context:
Question: what's the number of parishes founded in the 1800s? 
Thought 1: I need to retrieve the parishes founded 1800s. Since the time 1800s spans from 1800 to 1899, I need to retrieve the parishes whose Found time is between 1800-1899.
Action 1: Retrieve[retrieve the parishes whose Found time is between 1800-1899]
Observation 1: | Parish | Founded |
| St Mary | 1852 |
| Our Immaculate Mother & St Anselm | 1860 |
| The Immaculate Conception | 1854 |
| St James the Less | 1828 |
Thought 2: I need to count the number of parishes retrieved in observation 1.
Action 2: Calculate[count the number of parishes in the table of observation 1]
Observation 2: 4
Thought 3: The question asks the number of parishes founded in 1800s. The answer is obtained in observation 2, which is 4.
Action 2: Finish[4]

Table:
| Election | Number of popular votes | % of popular votes | Total elected seats | +/− |
| 1988 | 139,982 | 22.16 | 61 / 264 | |
| 1991 | 170,757 | 32.11 | 83 / 272 | 22 |
| 1994 | 242,557 | 35.34 | 121 / 346 | 38 |
| 1999 | 271,251 | 33.45 | 122 / 390 | 1 |
| 2003 | 459,640 | 44.67 | 194 / 400 | 72 |
| 2007 | 445,781 | 39.15 | 127 / 405 | 30 |
| 2011 | 464,512 | 39.34 | 103 / 412 | 18 |
Context:
Question: what is the total number of popular votes cast in 2003?
Thought 1: I need to retrieve the number of popular votes in the election year 2003.
Action 1: Retrieve[retrieve the number of popular votes in the election year 2003]
Observation 1: | Election | Number of popular votes |
| 2003 | 459,640 |
Thought 2: The question asks the total number of popular votes in 2003. The answer is retrieved from the observation 1, which is 459,640.
Action 2: Finish[459,640]

Table:
| Year | Competition | Venue | Position | Notes |
| 1989 | European Indoor Championships | The Hague, Netherlands | 10th |  |
| 1989 | World Indoor Championships | Budapest, Hungary | 9th |  | 
| 1991 | World Indoor Championships | Lisbon, Portugal | 6th |  |
| 1991 | World Championships | Tokyo, Japan | 5th | 5.75 m NR |
| 1992 | European Indoor Championships | Genoa, Italy | 5th |  |
| 1993 | World Championships | Stuttgart, Germany | 11th |  |
Context:
Question: what peter widen's is the highest finish in all indoor championships?
Thought 1: I need to retrieve peter widen's finish position from the table.
Action 1: Retrieve[retrieve peter widen's position from the table]
Observation 1: | Position |
| 10th |
| 9th |
| 6th |
| 5th |
| 5th |
| 11th |
Thought 2: The question asks the highest finish. Since I already have a table of finished position in observation 1, I need to compare the finished positions to find out the highest ranking, which is 5th.
Action 2: Finish[5th]
"""

DEMO_CRT_DIRECT = """
Table: 
| rank | player | county | tally | total | matches | average |
| 1 | pádraig horan | offaly | 5 - 17 | 32 | 4 | 8 |
| 2 | billy fitzpatrick | kilkenny | 2 - 24 | 30 | 4 | 7.5 |
| 3 | tony o ’sullivan | cork | 0 - 28 | 28 | 4 | 7 |
| 4 | p j molloy | galway | 3 - 11 | 20 | 2 | 10 |
| 5 | christy heffernan | kilkenny | 3 - 9 | 18 | 4 | 4.5 |
| 5 | pat horgan | cork | 0 - 18 | 18 | 4 | 4.5 |
Context: Table tittle: 1982 all - ireland senior hurling championship
Question: How many players in the 1982 all-Ireland senior hurling championship had a higher average score per game than the overall average score per game of the competition?
Answer: Let's think step by step. I need to first calculate the overall average score (8+7.5+7+10+4.5+4.5)/6=6.9<redacted-phone>667. Then I need to count how many players have scores larger than that. The first four players in the table have scores larger than 6.92. Therefore, the answer is: 4.

Table: 
| season | competition | round | opponent | home | away |
| 2013 - 14 | uefa europa league | 3q | hapoel ramat gan | 0 - 0 | 1 - 0 |
| 2013 - 14 | uefa europa league | play - off | pasching | 2 - 0 | 2 - 1 |
| 2013 - 14 | uefa europa league | group h | sevilla | 1 - 2 | - |
| 2013 - 14 | uefa europa league | group h | slovan liberec | - | 1 - 2 |
| 2013 - 14 | uefa europa league | group h | freiburg | - | 1 - 1 |
Context: Table title: g.d. estoril praia.
Question: Was there a correlation between GD Estoril Praia’s performance in home games and away games during the 2013-14 UEFA Europa League competition?  Answer with only ’Yes’ or ’No’ that is most accurate and nothing else.
Answer: Let's think step by step. Looking at the column home and column away, I do not find any correlation between the two columns. Therefore, the answer is: No.

Table: 
| Country | Export 2021(million) | Export 2020(million) |
| U.S.A | 2,827,816 | 2,308,471 |
| Canada | 485,700 | 405,577 |
| France | 683,817 | 552,748 |
| Germany | 1,353,626 | 1,092,044 |
Context: Table title: Export of different countries.
Question: Which country in Europe features the largest percentage change in export number between 2021 and 2020?
Answer: Let's think step by step. I need to calculate the percentage change for the countries in Europe, which are France and Germany. The percentage change for France is (683817-552748)/552748=23.71%. The percentage change for Germany is (1353626-1092044)/1092044=23.95%. Therefore, the answer is: Germany.

[BREAK]
Dataframe code: import pandas as pd\ndata={'rank':[1.0, 2.0, 3.0, 4.0, 5.0, 5.0],'player':['pádraig horan', 'billy fitzpatrick', 'tony o ’sullivan', 'p j molloy', 'christy heffernan', 'pat horgan'],'county':['offaly', 'kilkenny', 'cork', 'galway', 'kilkenny', 'cork'],'tally':['5 - 17', '2 - 24', '0 - 28', '3 - 11', '3 - 9', '0 - 18'],'total':[32.0, 30.0, 28.0, 20.0, 18.0, 18.0],'matches':[4.0, 4.0, 4.0, 2.0, 4.0, 4.0],'average':[8.0, 7.5, 7.0, 10.0, 4.5, 4.5]}\ndf=pd.DataFrame(data)
Context: Table tittle: 1982 all - ireland senior hurling championship
Question: How many players in the 1982 all-Ireland senior hurling championship had a higher average score per game than the overall average score per game of the competition?
Code:  ```Python
total_scores = df['average'].sum()  
total_matches =  len(df['total'])
overall_average_score_per_game = total_scores / total_matches  
# Count the number of players with a higher average than the competition's overall average  
players_higher_average = df[df['average'] > overall_average_score_per_game]  
result = len(players_higher_average)
```

Dataframe code: import pandas as pd\ndata={'season':['2013 - 14', '2013 - 14', '2013 - 14', '2013 - 14', '2013 - 14'],'competition':['uefa europa league', 'uefa europa league', 'uefa europa league', 'uefa europa league', 'uefa europa league'],'round':['3q', 'play - off', 'group h', 'group h', 'group h'],'opponent':['hapoel ramat gan', 'pasching', 'sevilla', 'slovan liberec', 'freiburg'],'home':['0 - 0', '2 - 0', '1 - 2', '-', '-'],'away':['1 - 0', '2 - 1', '-', '1 - 2', '1 - 1']}\ndf=pd.DataFrame(data)
Context: Table title: g.d. estoril praia.
Question: Was there a correlation between GD Estoril Praia’s performance in home games and away games during the 2013-14 UEFA Europa League competition?  Answer with only ’Yes’ or ’No’ that is most accurate and nothing else.
Code:  ```Python
def calculate_gd(score):  
    if score == '-':  # No game or no score reported  
        return None  
    goals = [int(x.strip()) for x in score.split('-')]  
    return goals[0] - goals[1]  
  
# Calculate goal difference for home and away games  
df['home_gd'] = df['home'].apply(calculate_gd)  
df['away_gd'] = df['away'].apply(calculate_gd)  
# Drop rows where either home_gd or away_gd is None (missing data)  
df.dropna(subset=['home_gd', 'away_gd'], inplace=True)  
# Check for correlation between home and away goal differences  
correlation = df['home_gd'].corr(df['away_gd'])  
# Executed results  
result = "Yes" if abs(correlation) > 0 else "No"
```

Dataframe code: import pandas as pd\ndata={'Country':['U.S.A', 'Canada', 'France', 'Germany'],'Export 2021(million)':['2,827,816', '485,700', '683,817', '1,353,626'],'Export 2020(million)':['2,308,471', '405,577', '552,748', '1,092,044']}\ndf=pd.DataFrame(data)
Context: Table title: Export of different countries.
Question: Which country in Europe features the largest percentage change in export number between 2021 and 2020?
Code:  ```Python
# Convert the export columns to numeric after removing commas  
df['Export 2021(million)'] = pd.to_numeric(df['Export 2021(million)'].str.replace(',', ''))  
df['Export 2020(million)'] = pd.to_numeric(df['Export 2020(million)'].str.replace(',', ''))  
# Calculate the percentage change between 2021 and 2020  
df['Percentage Change'] = ((df['Export 2021(million)'] - df['Export 2020(million)']) / df['Export 2020(million)']) * 100  
# Filter out the non-European countries and the empty string (missing country)  
european_countries = ['France', 'Germany']  # List of European countries in the DataFrame  
df_europe = df[df['Country'].isin(european_countries)]  
# Find the country with the largest percentage change  
largest_change_country = df_europe.loc[df_europe['Percentage Change'].idxmax()]  
# Executed results  
result = largest_change_country['Country']
```
"""


DEMO_CRT = """ Table: 
| rank | player | county | tally | total | matches | average |
| 1 | pádraig horan | offaly | 5 - 17 | 32 | 4 | 8 |
| 2 | billy fitzpatrick | kilkenny | 2 - 24 | 30 | 4 | 7.5 |
| 3 | tony o ’sullivan | cork | 0 - 28 | 28 | 4 | 7 |
| 4 | p j molloy | galway | 3 - 11 | 20 | 2 | 10 |
| 5 | christy heffernan | kilkenny | 3 - 9 | 18 | 4 | 4.5 |
| 5 | pat horgan | cork | 0 - 18 | 18 | 4 | 4.5 |
Table tittle: 1982 all - ireland senior hurling championship
Question: How many players in the 1982 all-Ireland senior hurling championship had a higher average score per game than the overall average score per game of the competition?
Thought 1: I need to first get the overall average score per game of the competition.
Action 1: Calculate[Calculate the overall average score of the competition.]
Observation 1: 6.9<redacted-phone>667
Thought 2: I need to retrieve the part of the table that only includes players with a higher average score than 6.9<redacted-phone>667. 
Action 2: Retrieve[Retrieve rows where the average score is larger than 6.92.]
Observation 2: | rank | player | county | tally | total | matches | average |
| 1 | pádraig horan | offaly | 5 - 17 | 32 | 4 | 8 |
| 2 | billy fitzpatrick | kilkenny | 2 - 24 | 30 | 4 | 7.5 |
| 3 | tony o ’sullivan | cork | 0 - 28 | 28 | 4 | 7 |
| 4 | p j molloy | galway | 3 - 11 | 20 | 2 | 10 |
Thought 3: I need to count the number of rows in the observatoin 2.
Action 3: Calculate[Count the number of rows in the observation 2.]
Observation 3: 4.
Thought 4: The question asks the number of players having a larger average scores than the overall average score. From the observation 3, we know that the answer is 4.
Action 4: Finish[4.]

Table: 
| season | competition | round | opponent | home | away |
| 2013 - 14 | uefa europa league | 3q | hapoel ramat gan | 0 - 0 | 1 - 0 |
| 2013 - 14 | uefa europa league | play - off | pasching | 2 - 0 | 2 - 1 |
| 2013 - 14 | uefa europa league | group h | sevilla | 1 - 2 | - |
| 2013 - 14 | uefa europa league | group h | slovan liberec | - | 1 - 2 |
| 2013 - 14 | uefa europa league | group h | freiburg | - | 1 - 1 |
Table title: g.d. estoril praia.
Question: Was there a correlation between GD Estoril Praia’s performance in home games and away games during the 2013-14 UEFA Europa League competition?  Answer with only ’Yes’ or ’No’ that is most accurate and nothing else.
Thought 1: I need to first find the column of "home" and "away".
Action 1: Retrieve[Retrieve the the column of "home" and "away".]
Observation 1: | home | away |
| 0 - 0 | 1 - 0 |
| 2 - 0 | 2 - 1 |
| 1 - 2 | - |
| - | 1 - 2 |
| - | 1 - 1 |
Thought 2: I need to compare the outcome of "home" and "away" games in observation 1. I do not find there is no correlation between "home" and "away" games. As a result, the answer is No.
Action 2: Finish[No.]

Table: 
| Country | Export 2021(million) | Export 2020(million) |
| U.S.A | 2,827,816 | 2,308,471 |
| Canada | 485,700 | 405,577 |
| France | 683,817 | 552,748 |
| Germany | 1,353,626 | 1,092,044 |
Table title: Export of different countries.
Question: Which country in Europe features the largest percentage change in export number between 2021 and 2020?
Thought 1: I need to identify the rows where the countries are in Europe. Since France and Germany are in Europe, I need to extract rows where the countries is either France or Germany.
Action 1: Retrieve[Retrieve tne rows where the country are either France or Germany.]
Observation 1: | Country | Export_2021 | Export_2020 | 
| France| 683,817  | 552,748 |
| Germany | 1,353,626  | 1,092,044 |
Thought 2: I need to calculate the percentage change of export between 2021 and 2022 in observation 1.
Action 2:  Calculate[Calculate tne percentage change between 2021 and 2022 in the observation 1.]
Observation 2: | Country | Percentage_change |
| France| 23.71 |
| Germany | 23.95 |
Thought 3: The question asks the country in Europe that features the largest percentage change. From observation 2, we can see that Germany has the largest percentage change 23.95%. Therefore the answer is Germany.
Action 3: Finish[Germany.] 

"""

DEMO_TAT_DIRECT = """Table:
 |  | 2019 |  | 2018
In thousands | $ | % | $ | %
Drinkable Kefir other than ProBugs | $ 71,822 | 77% | $ 78,523 | 76%
Cheese | 11,459 | 12% | 11,486 | 11%
Cream and other | 4,228 | 4% | 5,276 | 5%
ProBugs Kefir | 2,780 | 3% | 2,795 | 3%
Other dairy | 1,756 | 2% | 3,836 | 4%
Frozen Kefir (a) | 1,617 | 2% | 1,434 | 1%
Net Sales | $ 93,662 | 100% | $ 103,350 | 100%
Context: Paragraph 1: Our product categories are: Paragraph 2: Drinkable Kefir, sold in a variety of organic and non-organic sizes, flavors, and types, including low fat, non-fat, whole milk, protein, and BioKefir (a 3.5 oz. kefir with additional probiotic cultures). Paragraph 3: European-style soft cheeses, including farmer cheese in resealable cups. Paragraph 4: Cream and other, which consists primarily of cream, a byproduct of making our kefir. Paragraph 5: ProBugs, a line of kefir products designed for children. Paragraph 6: Other Dairy, which includes Cupped Kefir and Icelandic Skyr, a line of strained kefir and yogurt products in resealable cups. Paragraph 7: Frozen Kefir, available in soft serve and pint-size containers. Paragraph 8: Lifeway has determined that it has one reportable segment based on how our chief operating decision maker manages the business and in a manner consistent with the internal reporting provided to the chief operating decision maker. The chief operating decision maker, who is responsible for allocating resources and assessing our performance, has been identified collectively as the Chief Financial Officer, the Chief Operating Officer, the Chief Executive Officer, and Chairperson of the board of directors. Substantially all of our consolidated revenues relate to the sale of cultured dairy products that we produce using the same processes and materials and are sold to consumers through a common network of distributors and retailers in the United States. Paragraph 9: Net sales of products by category were as follows for the years ended December 31: Paragraph 10: (a) Includes Lifeway Kefir Shop sales Paragraph 11: Significant Customers – Sales are predominately to companies in the retail food industry located within the United States. Two major customers accounted for approximately 22% and 21% of net sales for the years ended December 31, 2019 and 2018, respectively. Two major customers accounted for approximately 17% of accounts receivable as of December 31, 2019 and 2018. Our ten largest customers as a group accounted for approximately 57% and 59% of net sales for the years ended December 31, 2019 and 2018, respectively. 
Question: What is the change in the net sales for cheese between 2018 and 2019?
Answer: Let's think step by step. I need to calculate the change of the net sames for cheese for 2019 to 2018, which is 11459 - 11486 = -27. Therefore, the answer is: -27.

Table:
 |  | Three Months Ended |  | % Variation | 
 | December 31, 2019 | September 29, 2019 | December 31, 2018 | Sequential | Year-Over-Year
 |  |  | (Unaudited, in millions) |  | 
Automotive and Discrete Group (ADG) | $924 | $894 | $967 | 3.3% | (4.5)%
Analog, MEMS and Sensors Group (AMS) | 1,085 | 968 | 988 | 12.1 | 9.9
Microcontrollers and Digital ICs Group (MDG) | 742 | 688 | 689 | 7.9 | 7.6
Others | 3 | 3 | 4 | — | —
Total consolidated net revenues | $2,754 | $2,553 | $2,648 | 7.9% | 4.0%
Context: Paragraph 1: On a sequential basis, ADG revenues were up 3.3%, driven by an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix. Paragraph 2: AMS revenues increased 12.1% driven by Analog and Imaging products. AMS increase was due to an increase of approximately 5% in average selling prices, entirely due to product mix, and to higher volumes of approximately of 7%. Paragraph 3: MDG revenues increased by 7.9%, mainly driven by Microcontrollers, due to both higher average selling prices of approximately 6%, entirely due to product mix, and higher volumes of approximately 2%. Paragraph 4: On a year-over-year basis, fourth quarter net revenues increased by 4.0%. ADG revenues decreased 4.5% compared to the year-ago quarter on lower revenues in both Automotive and Power Discrete. The decrease was entirely due to lower average selling prices of approximately 4%, while volumes remained substantially flat. The decrease in average selling prices was a combination of less favorable product mix and lower selling prices. Paragraph 5: AMS fourth quarter revenues grew 9.9% year-over-year, mainly driven by Analog and Imaging. The increase was entirely due to higher average selling prices of approximately 18%, entirely attributable to product mix, partially offset by lower volumes of approximately 8%. MDG fourth quarter revenues increased by 7.6%, mainly driven by Microcontrollers. The increase was due to higher average selling prices of approximately 9%, entirely due to improved product mix. 
Question: What led to increase in the revenue of ADG on sequential basis?
Answer: Let's think step by step. I need to look up what led to increase in the revenue of ADG on sequential basis in the context, which is: On a sequential basis, ADG revenues were up 3.3%, driven by an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix. Therefore, the answer is: an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix.

Table:
 | March 31, | 
 | 2019 | 2018
Raw materials | $74.5 | $26.0
Work in process | 413.0 | 311.8
Finished goods | 224.2 | 138.4
Total inventories | $711.7 | $476.2
Context: Paragraph 1: Inventories Paragraph 2: The components of inventories consist of the following (in millions): Paragraph 3: Inventories are valued at the lower of cost and net realizable value using the first-in, first-out method. Inventory impairment charges establish a new cost basis for inventory and charges are not subsequently reversed to income even if circumstances later suggest that increased carrying amounts are recoverable. 
Question: What was the percentage change in total inventories between 2018 and 2019?
Answer: Let's think step by step. I need to calculate the percentage change of total inventories between 2018 and 2019, which is[((711.7-476.2)/476.2)*100]=49.45%. Therefore, the answer is: 49.45%

[BREAK]
Dataframe code: import pandas as pd\ndata={'column 1':['In thousands', 'Drinkable Kefir other than ProBugs', 'Cheese', 'Cream and other', 'ProBugs Kefir', 'Other dairy', 'Frozen Kefir (a)', 'Net Sales'],'column 2':['$', '$ 71,822', '11,459', '4,228', '2,780', '1,756', '1,617', '$ 93,662'],'2019':['%', '77%', '12%', '4%', '3%', '2%', '2%', '100%'],'column 4':['$', '$ 78,523', '11,486', '5,276', '2,795', '3,836', '1,434', '$ 103,350'],'2018':['%', '76%', '11%', '5%', '3%', '4%', '1%', '100%']}\ndf=pd.DataFrame(data)
Context: Paragraph 1: Our product categories are: Paragraph 2: Drinkable Kefir, sold in a variety of organic and non-organic sizes, flavors, and types, including low fat, non-fat, whole milk, protein, and BioKefir (a 3.5 oz. kefir with additional probiotic cultures). Paragraph 3: European-style soft cheeses, including farmer cheese in resealable cups. Paragraph 4: Cream and other, which consists primarily of cream, a byproduct of making our kefir. Paragraph 5: ProBugs, a line of kefir products designed for children. Paragraph 6: Other Dairy, which includes Cupped Kefir and Icelandic Skyr, a line of strained kefir and yogurt products in resealable cups. Paragraph 7: Frozen Kefir, available in soft serve and pint-size containers. Paragraph 8: Lifeway has determined that it has one reportable segment based on how our chief operating decision maker manages the business and in a manner consistent with the internal reporting provided to the chief operating decision maker. The chief operating decision maker, who is responsible for allocating resources and assessing our performance, has been identified collectively as the Chief Financial Officer, the Chief Operating Officer, the Chief Executive Officer, and Chairperson of the board of directors. Substantially all of our consolidated revenues relate to the sale of cultured dairy products that we produce using the same processes and materials and are sold to consumers through a common network of distributors and retailers in the United States. Paragraph 9: Net sales of products by category were as follows for the years ended December 31: Paragraph 10: (a) Includes Lifeway Kefir Shop sales Paragraph 11: Significant Customers – Sales are predominately to companies in the retail food industry located within the United States. Two major customers accounted for approximately 22% and 21% of net sales for the years ended December 31, 2019 and 2018, respectively. Two major customers accounted for approximately 17% of accounts receivable as of December 31, 2019 and 2018. Our ten largest customers as a group accounted for approximately 57% and 59% of net sales for the years ended December 31, 2019 and 2018, respectively.
Question: What is the change in the net sales for cheese between 2018 and 2019?
Code:  ```Python
# Remove dollar signs and commas from the sales figures, and convert to numeric  
df['2019_sales'] = pd.to_numeric(df['column 2'].str.replace('$', '').str.replace(',', '').str.strip())  
df['2018_sales'] = pd.to_numeric(df['column 4'].str.replace('$', '').str.replace(',', '').str.strip())  
# Find the row for Cheese  
cheese_row = df[df['column 1'] == 'Cheese']  
# Calculate the change in net sales for cheese between 2018 and 2019  
result = cheese_row['2019_sales'].values[0] - cheese_row['2018_sales'].values[0]  
```

Dataframe code: import pandas as pd\ndata={'column 1':['', '', 'Automotive and Discrete Group (ADG)', 'Analog, MEMS and Sensors Group (AMS)', 'Microcontrollers and Digital ICs Group (MDG)', 'Others', 'Total consolidated net revenues'],'column 2':['December 31, 2019', '', '$924', '1,085', 742.0, 3.0, '$2,754'],'Three Months Ended':['September 29, 2019', '', '$894', 968.0, 688.0, 3.0, '$2,553'],'column 4':['December 31, 2018', '(Unaudited, in millions)', '$967', 988.0, 689.0, 4.0, '$2,648'],'% Variation':['Sequential', '', '3.3%', 12.1, 7.9, '—', '7.9%'],'column 6':['Year-Over-Year', '', '(4.5)%', 9.9, 7.6, '—', '4.0%']}\ndf=pd.DataFrame(data)
Context: Paragraph 1: On a sequential basis, ADG revenues were up 3.3%, driven by an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix. Paragraph 2: AMS revenues increased 12.1% driven by Analog and Imaging products. AMS increase was due to an increase of approximately 5% in average selling prices, entirely due to product mix, and to higher volumes of approximately of 7%. Paragraph 3: MDG revenues increased by 7.9%, mainly driven by Microcontrollers, due to both higher average selling prices of approximately 6%, entirely due to product mix, and higher volumes of approximately 2%. Paragraph 4: On a year-over-year basis, fourth quarter net revenues increased by 4.0%. ADG revenues decreased 4.5% compared to the year-ago quarter on lower revenues in both Automotive and Power Discrete. The decrease was entirely due to lower average selling prices of approximately 4%, while volumes remained substantially flat. The decrease in average selling prices was a combination of less favorable product mix and lower selling prices. Paragraph 5: AMS fourth quarter revenues grew 9.9% year-over-year, mainly driven by Analog and Imaging. The increase was entirely due to higher average selling prices of approximately 18%, entirely attributable to product mix, partially offset by lower volumes of approximately 8%. MDG fourth quarter revenues increased by 7.6%, mainly driven by Microcontrollers. The increase was due to higher average selling prices of approximately 9%, entirely due to improved product mix. 
Question: What led to increase in the revenue of ADG on sequential basis?
Code:  ```Python
result = "an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix."
```

Dataframe code: import pandas as pd\ndata={'column 1':['', 'Raw materials', 'Work in process', 'Finished goods', 'Total inventories'],'March 31,':[2019.0, '$74.5', 413.0, 224.2, '$711.7'],'column 3':[2018.0, '$26.0', 311.8, 138.4, '$476.2']}\ndf=pd.DataFrame(data)
Context: Paragraph 1: Inventories Paragraph 2: The components of inventories consist of the following (in millions): Paragraph 3: Inventories are valued at the lower of cost and net realizable value using the first-in, first-out method. Inventory impairment charges establish a new cost basis for inventory and charges are not subsequently reversed to income even if circumstances later suggest that increased carrying amounts are recoverable. 
Question: What was the percentage change in total inventories between 2018 and 2019?
Code:  ```Python
# Convert the total inventories to numeric values after removing the dollar sign  
df['2019_inventories'] = pd.to_numeric(df['March 31,'].str.replace('$', '').str.replace(',', '').str.strip(), errors='coerce')  
df['2018_inventories'] = pd.to_numeric(df['column 3'].str.replace('$', '').str.replace(',', '').str.strip(), errors='coerce')  
# Find the row for Total inventories  
total_inventories_row = df[df['column 1'] == 'Total inventories']  
# Calculate the change in total inventories between 2018 and 2019  
change_in_inventories = total_inventories_row['2019_inventories'].values[0] - total_inventories_row['2018_inventories'].values[0]  
# Calculate the percentage change in total inventories  
result = (change_in_inventories / total_inventories_row['2018_inventories'].values[0]) * 100  
```
"""


DEMO_TAT = """Example 1:
Table:
 |  | 2019 |  | 2018
In thousands | $ | % | $ | %
Drinkable Kefir other than ProBugs | $ 71,822 | 77% | $ 78,523 | 76%
Cheese | 11,459 | 12% | 11,486 | 11%
Cream and other | 4,228 | 4% | 5,276 | 5%
ProBugs Kefir | 2,780 | 3% | 2,795 | 3%
Other dairy | 1,756 | 2% | 3,836 | 4%
Frozen Kefir (a) | 1,617 | 2% | 1,434 | 1%
Net Sales | $ 93,662 | 100% | $ 103,350 | 100%
Context: Paragraph 1: Our product categories are: Paragraph 2: Drinkable Kefir, sold in a variety of organic and non-organic sizes, flavors, and types, including low fat, non-fat, whole milk, protein, and BioKefir (a 3.5 oz. kefir with additional probiotic cultures). Paragraph 3: European-style soft cheeses, including farmer cheese in resealable cups. Paragraph 4: Cream and other, which consists primarily of cream, a byproduct of making our kefir. Paragraph 5: ProBugs, a line of kefir products designed for children. Paragraph 6: Other Dairy, which includes Cupped Kefir and Icelandic Skyr, a line of strained kefir and yogurt products in resealable cups. Paragraph 7: Frozen Kefir, available in soft serve and pint-size containers. Paragraph 8: Lifeway has determined that it has one reportable segment based on how our chief operating decision maker manages the business and in a manner consistent with the internal reporting provided to the chief operating decision maker. The chief operating decision maker, who is responsible for allocating resources and assessing our performance, has been identified collectively as the Chief Financial Officer, the Chief Operating Officer, the Chief Executive Officer, and Chairperson of the board of directors. Substantially all of our consolidated revenues relate to the sale of cultured dairy products that we produce using the same processes and materials and are sold to consumers through a common network of distributors and retailers in the United States. Paragraph 9: Net sales of products by category were as follows for the years ended December 31: Paragraph 10: (a) Includes Lifeway Kefir Shop sales Paragraph 11: Significant Customers – Sales are predominately to companies in the retail food industry located within the United States. Two major customers accounted for approximately 22% and 21% of net sales for the years ended December 31, 2019 and 2018, respectively. Two major customers accounted for approximately 17% of accounts receivable as of December 31, 2019 and 2018. Our ten largest customers as a group accounted for approximately 57% and 59% of net sales for the years ended December 31, 2019 and 2018, respectively. 
Question: What is the change in the net sales for cheese between 2018 and 2019?
Thought 1: I need to retrive the net sales for cheese for 2018 and 2019.
Action 1: Retrieve[net sales for cheese for 2018 and 2019]
Observation 1:  | 2019 | 2018 |
| 11459 | 11486 |
Thought 2: I need to substract the net sales for cheese for 2019 from 2018, which results in the formular: [11459 - 11486]
Action 2: Calculate[11459 - 11486]
Observation 2: -27
Thought 3: The question asks for change in the net sales for cheese between 2018 and 2019. The answer is in the observation 2, which is -27. The unit/scale of the number is thousand, therefore the answer is -27 thousands.
Action 3: Finish[-27 thousands]

Example 2:
Table:
 |  | Three Months Ended |  | % Variation | 
 | December 31, 2019 | September 29, 2019 | December 31, 2018 | Sequential | Year-Over-Year
 |  |  | (Unaudited, in millions) |  | 
Automotive and Discrete Group (ADG) | $924 | $894 | $967 | 3.3% | (4.5)%
Analog, MEMS and Sensors Group (AMS) | 1,085 | 968 | 988 | 12.1 | 9.9
Microcontrollers and Digital ICs Group (MDG) | 742 | 688 | 689 | 7.9 | 7.6
Others | 3 | 3 | 4 | — | —
Total consolidated net revenues | $2,754 | $2,553 | $2,648 | 7.9% | 4.0%
Context: Paragraph 1: On a sequential basis, ADG revenues were up 3.3%, driven by an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix. Paragraph 2: AMS revenues increased 12.1% driven by Analog and Imaging products. AMS increase was due to an increase of approximately 5% in average selling prices, entirely due to product mix, and to higher volumes of approximately of 7%. Paragraph 3: MDG revenues increased by 7.9%, mainly driven by Microcontrollers, due to both higher average selling prices of approximately 6%, entirely due to product mix, and higher volumes of approximately 2%. Paragraph 4: On a year-over-year basis, fourth quarter net revenues increased by 4.0%. ADG revenues decreased 4.5% compared to the year-ago quarter on lower revenues in both Automotive and Power Discrete. The decrease was entirely due to lower average selling prices of approximately 4%, while volumes remained substantially flat. The decrease in average selling prices was a combination of less favorable product mix and lower selling prices. Paragraph 5: AMS fourth quarter revenues grew 9.9% year-over-year, mainly driven by Analog and Imaging. The increase was entirely due to higher average selling prices of approximately 18%, entirely attributable to product mix,
partially offset by lower volumes of approximately 8%. MDG fourth quarter revenues increased by 7.6%, mainly driven by Microcontrollers. The increase was due to higher average selling prices of approximately 9%,
entirely due to improved product mix. 
Question: What led to increase in the revenue of ADG on sequential basis?
Thought 1: I need to look up what led to increase in the revenue of ADG on sequential basis in the context.
Action 1: Look up[what led to increase in the revenue of ADG on sequential basis] 
Observation 1: On a sequential basis, ADG revenues were up 3.3%, driven by an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix.
Thought 2: The question asks what led to increase in the revenue of ADG on sequential basis, the answer is in the observation 1, which is: an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix. There is no unit or scale needed as the answer is not a number.
Action 2: Finish[an increase in volumes of approximately 8%, partially offset by a decrease in average selling prices of approximately 5%, mostly attributable to product mix.]

Example 3:
Table:
 | March 31, | 
 | 2019 | 2018
Raw materials | $74.5 | $26.0
Work in process | 413.0 | 311.8
Finished goods | 224.2 | 138.4
Total inventories | $711.7 | $476.2
Context: Paragraph 1: Inventories Paragraph 2: The components of inventories consist of the following (in millions): Paragraph 3: Inventories are valued at the lower of cost and net realizable value using the first-in, first-out method. Inventory impairment charges establish a new cost basis for inventory and charges are not subsequently reversed to income even if circumstances later suggest that increased carrying amounts are recoverable. 
Question: What was the percentage change in total inventories between 2018 and 2019?
Thought 1: I need to retrieve the total inventories of 2018 and 2019 respectively.
Action 1: Retrieve[total inventories of 2018 and 2019]
Observation 1: | 2019 | 2018 |
| $711.7 | $476.2 |
Thought 2: I need to calculate the percentage using the formular: [((711.7-476.2)/476.2)*100]
Action 2: Calculate[((711.7-476.2)/476.2)*100]
Observation 2: 49.45.
Thought 3: The question asks for percentage change of total inventories between 2018 and 2019. The answer is in the observation 2, which is 49.45. The unit/scale of the number is percentage, therefore the answer is 49.45%
Action 3: Finish[49.45%]
"""

DEMO_SCITAB_DIRECT = """Table:
| Model | Training data | Overall | Easy | Hard |
| BERT-large-FT | B-COPA | 74.5 (±0.7) | 74.7 (±0.4) | 74.4 (±0.9) |
| BERT-large-FT | B-COPA (50%) | 74.3 (±2.2) | 76.8 (±1.9) | 72.8 (±3.1) |
| BERT-large-FT | COPA | 76.5 (±2.7) | 83.9 (±4.4) | 71.9 (±2.5) |
| RoBERTa-large-FT | B-COPA | 89.0 (±0.3) | 88.9 (±2.1) | 89.0 (±0.8) |
| RoBERTa-large-FT | B-COPA (50%) | 86.1 (±2.2) | 87.4 (±1.1) | 85.4 (±2.9) |
| RoBERTa-large-FT | COPA | 87.7 (±0.9) | 91.6 (±1.1) | 85.3 (±2.0) |
Context: Table caption: Table 5: Results of fine-tuned models on Balanced COPA. Easy: instances with superficial cues, Hard: instances without superficial cues.
Claim: RoBERTa-large outperforms BERT-large when fine-tuned on full and balanced COPA.
Question: Is the above claim true or false ?
Answer: Let's think step by step. I need to first retrieve the performance of RoBERTa-large and BERT-large on full and balanced COPA. The B-COPA is the acronym of the balanced COPA. Therefore, I need to retrieve Retrieve the rows where model is BERT-large-FT or RoBERTa-large-FT and training data is COPA or B-COPA.| Model | Training data | Overall | Easy | Hard |\n| BERT-large-FT | B-COPA | 74.5 (±0.7) | 74.7 (±0.4) | 74.4 (±0.9) |\n| BERT-large-FT | COPA | 76.5 (±2.7) | 83.9 (±4.4) | 71.9 (±2.5) |\n| RoBERTa-large-FT | COPA | 87.7 (±0.9) | 91.6 (±1.1) | 85.3 (±2.0) |\n| RoBERTa-large-FT | B-COPA | 89.0 (±0.3) | 88.9 (±2.1) | 89.0 (±0.8) |\n. RoBERTa-large has an overall performance of 87.7 on COPA. BERT-large has an overall performance of 76.5 on COPA. Therefore, RoBERTa-large outperforms BERT-large on COPA. For balanced COPA, RoBERTa-large has an overall performance of 89.0, BERT-large has an overall performance of 74.5. RoBERTa-large outperforms BERT-large on balanced COPA. Since RoBERTa-large outperforms BERT-large on both full and balanced COPA, the claim is true. Therefore, the answer is: True.

Table: 
| Model | Training data | Overall | Easy | Hard |
| BERT-large-FT | B-COPA | 74.5 (±0.7) | 74.7 (±0.4) | 74.4 (±0.9) |
| BERT-large-FT | B-COPA (50%) | 74.3 (±2.2) | 76.8 (±1.9) | 72.8 (±3.1) |
| BERT-large-FT | COPA | 76.5 (±2.7) | 83.9 (±4.4) | 71.9 (±2.5) |
| RoBERTa-large-FT | B-COPA | 89.0 (±0.3) | 88.9 (±2.1) | 89.0 (±0.8) |
| RoBERTa-large-FT | B-COPA (50%) | 86.1 (±2.2) | 87.4 (±1.1) | 85.4 (±2.9) |
| RoBERTa-large-FT | COPA | 87.7 (±0.9) | 91.6 (±1.1) | 85.3 (±2.0) |
Context: Table caption: Table 5: Results of fine-tuned models on Balanced COPA. Easy: instances with superficial cues, Hard: instances without superficial cues.
Claim: The difference between RoBERTa-large-FT and BERT-large-FT is 3.8 points on B-COPA, which is significantly smaller than the difference in COPA. 
Question: Is the above claim true or false? 
Answer: Let's think step by step. I need to first retrieve the performance of RoBERTa-large-FT and BERT-large-FT on B-COPA and COPA.| Model | Training data | Overall |\n| BERT-large-FT | B-COPA | 74.5 (±0.7) |\n| BERT-large-FT | COPA | 76.5 (±2.7) |\n| RoBERTa-large-FT | COPA | 87.7 (±0.9) |\n| RoBERTa-large-FT | B-COPA | 89.0 (±0.3) |\n The performance difference between RoBERTa-large-FT and BERT-large-FT on B-COPA is 89.0-74.5=14.5 The performance difference between RoBERTa-large-FT and BERT-large-FT on COPA is 87.7-76.5=11.2. The difference between RoBERTa-large-FT and BERT-large-FT on B-COPA is 14.5. This is significantly larger than the difference on COPA, which is 11.2. Therefore , the answer is: False.

Table: 
| Model | Joint Acc. |
| COMER | 88.64% |
| - Hierachical-Attn | 86.69% |
| - MLP | 83.24% |
Context: Table caption: Table 4: The ablation study on the WoZ 2.0 dataset with the joint goal accuracy on the test set. For ''- Hierachical-Attn '', we remove the residual connections between the attention modules in the CMR decoders and all the attention memory access are based on the output from the LSTM. For ``- MLP '', we further replace the MLP with a single linear layer with the non-linear activation.
Claim: [CONTINUE] The effectiveness of our hierarchical attention design is proved by an accuracy drop of 1.95% after removing residual connections and the hierarchical stack of our attention modules. 
Question: Is the above claim true or false?
Answer: Let's think step by step. I need to retrieve the perfomance of COMER with the hierarchical attention design, which is 88.64%. I need to retrieve the perfomance of COMER without the hierarchical attention design. From the context, I find that the model under the name ''- Hierachical - Attn '' corresponds to the model without the residual connections between the attention modules. So I need to retrieve the joint acc. of the model ''- Hierachical-Attn'', which is 86.69%. Then I calculate the difference in model performances of COMER and - Hierachical-Attn. 88.64-86.69=1.95. Since the claim mentioned after removing residual connections and the hierarchical stack of our attention modules, there is an accuracy drop of 1.95%. Therefore, the answer is: True.

[BREAK]
Dataframe code: import pandas as pd\ndata={'Model':['BERT-large-FT', 'BERT-large-FT', 'BERT-large-FT', 'RoBERTa-large-FT', 'RoBERTa-large-FT', 'RoBERTa-large-FT'],'Training data':['B-COPA', 'B-COPA (50%)', 'COPA', 'B-COPA', 'B-COPA (50%)', 'COPA'],'Overall':['74.5 (±0.7)', '74.3 (±2.2)', '76.5 (±2.7)', '89.0 (±0.3)', '86.1 (±2.2)', '87.7 (±0.9)'],'Easy':['74.7 (±0.4)', '76.8 (±1.9)', '83.9 (±4.4)', '88.9 (±2.1)', '87.4 (±1.1)', '91.6 (±1.1)'],'Hard':['74.4 (±0.9)', '72.8 (±3.1)', '71.9 (±2.5)', '89.0 (±0.8)', '85.4 (±2.9)', '85.3 (±2.0)']}\ndf=pd.DataFrame(data)
Context: Table caption: Table 5: Results of fine-tuned models on Balanced COPA. Easy: instances with superficial cues, Hard: instances without superficial cues.
Claim: RoBERTa-large outperforms BERT-large when fine-tuned on full and balanced COPA.
Question: Is the above claim true or false ?
Code:  ```Python
# Function to extract the mean performance score (ignoring the standard deviation)  
def extract_mean_performance(score_with_std):  
    if pd.isna(score_with_std):  
        return None  
    return float(score_with_std.split(' ')[0])  
# Compare performances for "B-COPA" and "COPA"  
bert_performance_bcopa = extract_mean_performance(df[(df['Model'] == 'BERT-large-FT') & (df['Training data'] == 'B-COPA')]['Overall'].values[0])  
roberta_performance_bcopa = extract_mean_performance(df[(df['Model'] == 'RoBERTa-large-FT') & (df['Training data'] == 'B-COPA')]['Overall'].values[0])  
bert_performance_copa = extract_mean_performance(df[(df['Model'] == 'BERT-large-FT') & (df['Training data'] == 'COPA')]['Overall'].values[0])  
roberta_performance_copa = extract_mean_performance(df[(df['Model'] == 'RoBERTa-large-FT') & (df['Training data'] == 'COPA')]['Overall'].values[0])  
# Check if RoBERTa-large outperforms BERT-large for both "B-COPA" and "COPA"  
claim_true_for_bcopa = roberta_performance_bcopa > bert_performance_bcopa  
claim_true_for_copa = roberta_performance_copa > bert_performance_copa  
# If RoBERTa-large outperforms BERT-large for both, the claim is true  
claim_true = claim_true_for_bcopa and claim_true_for_copa  
result = "True" if claim_true else "False"
```

Dataframe code: import pandas as pd\ndata={'Model':['BERT-large-FT', 'BERT-large-FT', 'BERT-large-FT', 'RoBERTa-large-FT', 'RoBERTa-large-FT', 'RoBERTa-large-FT'],'Training data':['B-COPA', 'B-COPA (50%)', 'COPA', 'B-COPA', 'B-COPA (50%)', 'COPA'],'Overall':['74.5 (±0.7)', '74.3 (±2.2)', '76.5 (±2.7)', '89.0 (±0.3)', '86.1 (±2.2)', '87.7 (±0.9)'],'Easy':['74.7 (±0.4)', '76.8 (±1.9)', '83.9 (±4.4)', '88.9 (±2.1)', '87.4 (±1.1)', '91.6 (±1.1)'],'Hard':['74.4 (±0.9)', '72.8 (±3.1)', '71.9 (±2.5)', '89.0 (±0.8)', '85.4 (±2.9)', '85.3 (±2.0)']}\ndf=pd.DataFrame(data)
Context: Table caption: Table 5: Results of fine-tuned models on Balanced COPA. Easy: instances with superficial cues, Hard: instances without superficial cues.
Claim: The difference between RoBERTa-large-FT and BERT-large-FT is 3.8 points on B-COPA, which is significantly smaller than the difference in COPA. 
Question: Is the above claim true or false? 
Code:  ```Python
# Function to extract the mean performance score (ignoring the standard deviation)  
def extract_mean_performance(score_with_std):  
    if pd.isna(score_with_std):  
        return None  
    return float(score_with_std.split(' ')[0])  
  
# Calculate the differences in performance scores  
bert_performance_bcopa = extract_mean_performance(df[(df['Model'] == 'BERT-large-FT') & (df['Training data'] == 'B-COPA')]['Overall'].values[0])  
roberta_performance_bcopa = extract_mean_performance(df[(df['Model'] == 'RoBERTa-large-FT') & (df['Training data'] == 'B-COPA')]['Overall'].values[0])  
bert_performance_copa = extract_mean_performance(df[(df['Model'] == 'BERT-large-FT') & (df['Training data'] == 'COPA')]['Overall'].values[0])  
roberta_performance_copa = extract_mean_performance(df[(df['Model'] == 'RoBERTa-large-FT') & (df['Training data'] == 'COPA')]['Overall'].values[0])  
# Calculate the performance score differences  
difference_bcopa = roberta_performance_bcopa - bert_performance_bcopa  
difference_copa = roberta_performance_copa - bert_performance_copa  
# Check if the difference for COPA is significantly larger than the difference for B-COPA  
claim_true = difference_bcopa == 3.8 and difference_copa > difference_bcopa  
result = "True" if claim_true else "False"
```

Dataframe code: import pandas as pd\ndata={'Model':['COMER', '- Hierachical-Attn', '- MLP'],'Joint Acc.':['88.64%', '86.69%', '83.24%']}\ndf=pd.DataFrame(data)
Context: Table caption: Table 4: The ablation study on the WoZ 2.0 dataset with the joint goal accuracy on the test set. For ''- Hierachical-Attn '', we remove the residual connections between the attention modules in the CMR decoders and all the attention memory access are based on the output from the LSTM. For ``- MLP '', we further replace the MLP with a single linear layer with the non-linear activation.
Claim: [CONTINUE] The effectiveness of our hierarchical attention design is proved by an accuracy drop of 1.95% after removing residual connections and the hierarchical stack of our attention modules. 
Question: Is the above claim true or false?
Code:  ```Python
# Function to convert percentage string to a float  
def percentage_to_float(percentage_str):  
    return float(percentage_str.strip('%'))  
# Extract the accuracy values for COMER and the model without hierarchical attention  
accuracy_comer = percentage_to_float(df.loc[df['Model'] == 'COMER', 'Joint Acc.'].values[0])  
accuracy_hierachical_attn_removed = percentage_to_float(df.loc[df['Model'] == '- Hierachical-Attn', 'Joint Acc.'].values[0])  
# Calculate the accuracy drop  
accuracy_drop = accuracy_comer - accuracy_hierachical_attn_removed  
# Check if the accuracy drop is 1.95%  
claim_true = abs(accuracy_drop - 1.95) < 0.01  # Using a small epsilon to account for floating point precision  
result = "True" if claim_true else "False" 
```

"""

DEMO_SCITAB = """Table:
| Model | Training data | Overall | Easy | Hard |
| BERT-large-FT | B-COPA | 74.5 (±0.7) | 74.7 (±0.4) | 74.4 (±0.9) |
| BERT-large-FT | B-COPA (50%) | 74.3 (±2.2) | 76.8 (±1.9) | 72.8 (±3.1) |
| BERT-large-FT | COPA | 76.5 (±2.7) | 83.9 (±4.4) | 71.9 (±2.5) |
| RoBERTa-large-FT | B-COPA | 89.0 (±0.3) | 88.9 (±2.1) | 89.0 (±0.8) |
| RoBERTa-large-FT | B-COPA (50%) | 86.1 (±2.2) | 87.4 (±1.1) | 85.4 (±2.9) |
| RoBERTa-large-FT | COPA | 87.7 (±0.9) | 91.6 (±1.1) | 85.3 (±2.0) |
Context: Table caption: Table 5: Results of fine-tuned models on Balanced COPA. Easy: instances with superficial cues, Hard: instances without superficial cues.
Claim: RoBERTa-large outperforms BERT-large when fine-tuned on full and balanced COPA.
Question: Is the above claim true or false ?
Thought 1: I need to retrieve the performance of RoBERTa-large and BERT-large on full and balanced COPA. The B-COPA is the acronym of the balanced COPA. Therefore, I need to retrieve Retrieve the rows where model is BERT-large-FT or RoBERTa-large-FT and training data is COPA or B-COPA.
Action 1: Retrieve[Retrieve the rows where model is BERT-large-FT or RoBERTa-large-FT and training data is COPA or B-COPA.]
Observation 1: | Model | Training data | Overall | Easy | Hard |
| BERT-large-FT | B-COPA | 74.5 (±0.7) | 74.7 (±0.4) | 74.4 (±0.9) |
| BERT-large-FT | COPA | 76.5 (±2.7) | 83.9 (±4.4) | 71.9 (±2.5) |
| RoBERTa-large-FT | COPA | 87.7 (±0.9) | 91.6 (±1.1) | 85.3 (±2.0) |
| RoBERTa-large-FT | B-COPA | 89.0 (±0.3) | 88.9 (±2.1) | 89.0 (±0.8) |
Thought 2: In observation 1, RoBERTa-large has an overall performance of 87.7 on COPA. BERT-large has an overall performance of 76.5 on COPA. Therefore, RoBERTa-large outperforms BERT-large on COPA. For balanced COPA, RoBERTa-large has an overall performance of 89.0, BERT-large has an overall performance of 74.5. RoBERTa-large outperforms BERT-large on balanced COPA. Since RoBERTa-large outperforms BERT-large on both full and balanced COPA, the claim is true.
Action 2: Finish[True]

Table: 
| Model | Training data | Overall | Easy | Hard |
| BERT-large-FT | B-COPA | 74.5 (±0.7) | 74.7 (±0.4) | 74.4 (±0.9) |
| BERT-large-FT | B-COPA (50%) | 74.3 (±2.2) | 76.8 (±1.9) | 72.8 (±3.1) |
| BERT-large-FT | COPA | 76.5 (±2.7) | 83.9 (±4.4) | 71.9 (±2.5) |
| RoBERTa-large-FT | B-COPA | 89.0 (±0.3) | 88.9 (±2.1) | 89.0 (±0.8) |
| RoBERTa-large-FT | B-COPA (50%) | 86.1 (±2.2) | 87.4 (±1.1) | 85.4 (±2.9) |
| RoBERTa-large-FT | COPA | 87.7 (±0.9) | 91.6 (±1.1) | 85.3 (±2.0) |
Context: Table caption: Table 5: Results of fine-tuned models on Balanced COPA. Easy: instances with superficial cues, Hard: instances without superficial cues.
Claim: The difference between RoBERTa-large-FT and BERT-large-FT is 3.8 points on B-COPA, which is significantly smaller than the difference in COPA. 
Question: Is the above claim true or false? 
Thought 1: I need to retrieve the performance of RoBERTa-large-FT and BERT-large-FT on B-COPA and COPA.
Action 1: Retrieve[Retrieve the overall performance of BERT-large-FT and RoBERTa-large-FT on COPA and B-COPA training set.]
Observation 1: | Model | Training data | Overall |
| BERT-large-FT | B-COPA | 74.5 (±0.7) |
| BERT-large-FT | COPA | 76.5 (±2.7) |
| RoBERTa-large-FT | COPA | 87.7 (±0.9) |
| RoBERTa-large-FT | B-COPA | 89.0 (±0.3) |
Thought 2: I need to compare the performance difference between RoBERTa-large-FT and BERT-large-FT on B-COPA.
Action 2: Calculate[89.0-74.5]
Observation 2: 14.5
Thought 3: I need to compare the performance difference between RoBERTa-large-FT and BERT-large-FT on COPA.
Action 3: Calculate[87.7-76.5]
Observation 3: 11.2
Thought 4: The difference between RoBERTa-large-FT and BERT-large-FT on B-COPA is 14.5 (in the observation 2). This is significantly larger than the difference on COPA, which is 11.2 (in the observation 3). Therefore , the Claim is false.
Action 4: Finish[False]

Table: 
| Model | Joint Acc. |
| COMER | 88.64% |
| - Hierachical-Attn | 86.69% |
| - MLP | 83.24% |
Context: Table caption: Table 4: The ablation study on the WoZ 2.0 dataset with the joint goal accuracy on the test set. For ''- Hierachical-Attn '', we remove the residual connections between the attention modules in the CMR decoders and all the attention memory access are based on the output from the LSTM. For ``- MLP '', we further replace the MLP with a single linear layer with the non-linear activation.
Claim: [CONTINUE] The effectiveness of our hierarchical attention design is proved by an accuracy drop of 1.95% after removing residual connections and the hierarchical stack of our attention modules. 
Question: Is the above claim true or false?
Thought 1: I need to retrieve the perfomance of COMER with the hierarchical attention design.
Action 1: Retrieve[Retrieve the joint acc. of the model COMER.]
Observation 1: 88.64%.
Thought 2: I need to retrieve the perfomance of COMER without the hierarchical attention design. From the context, I find that the model under the name ''- Hierachical - Attn '' corresponds to the model without the residual connections between the attention modules. So I need to retrieve the joint acc. of the model ''- Hierachical-Attn''.
Action 2: Retrieve[Retrieve the joint acc. of the model - Hierachical-Attn.]
Observation 2: 86.69%.
Thought 3: I need to calculate the difference in model performances of COMER and - Hierachical-Attn.
Action 3: Calculate[88.64-86.69]
Observation 3: 1.95.
Thought 4: Since the claim mentioned after removing residual connections and the hierarchical stack of our attention modules, there is an accuracy drop of 1.95%. This is true from the observation 3. Therefore the claim is True.
Action 4: Finish[True]
"""


DEMO_DATABENCH = """Table:
| rank | personName | age | finalWorth | category | source | country | state | city | organization | selfMade | gender | birthDate | title | philanthropyScore | bio | about |
| 1 | Elon Musk | 50.0 | 219000 | Automotive | Tesla, SpaceX | United States | Texas | Austin | Tesla | True | M | 1971-06-28 00:00:00+00:00 | CEO | 1.0 | Elon Musk is working to revolutionize transportation both on Earth, through electric car maker Tesla -- and in space, via rocket producer SpaceX. He owns 21% of Tesla but has pledged more than half his stake as collateral for loans; Forbes has discounted his stake to take the loans into account. A regulatory filing in early April 2022 revealed that Musk had purchased 9.2% of Twitter. The company invited him to join its board the next day.   SpaceX, Musk's rocket company, is valued at $74 billion after a funding round in February 2021. He grew up in South Africa, then immigrated to Canada at age 17. He landed in the U.S. as a transfer student to the University of Pennsylvania. | Musk was accepted to a graduate program at Stanford, but deferred attendance to launch his first business, software company Zip2. As a kid Musk taught himself to code; he sold his first game, Blastar, for about $500.  |
| 2 | Jeff Bezos | 58.0 | 171000 | Technology | Amazon | United States | Washington | Seattle | Amazon | True | M | 1964-01-12 00:00:00+00:00 | Entrepreneur | 1.0 | Jeff Bezos founded e-commerce giant Amazon in 1994 out of his garage in Seattle. He stepped down as CEO to become executive chairman in July 2021. Bezos sold $8.8 billion worth of his Amazon stock in 2021 and also gave some shares away; he now owns a bit less than 10% of the company.  Amazon faced criticism from U.S. senators and the general public for its treatment of warehouse workers during the coronavirus pandemic. He and his wife MacKenzie divorced in 2019 after 25 years of marriage and he transferred a quarter of his then-16% Amazon stake to her. Bezos owns The Washington Post and Blue Origin, an aerospace company developing rockets; he briefly flew to space in one in July 2021. | Growing up, Jeff Bezos worked summers on his grandfather's ranch repairing Caterpillar tractors. Bezos met Google founders Larry Page and Sergey Brin in 1998 and managed to become one of the company's first angel investors, putting in an estimated $250,000. |
...[remaining 2665 rows unshown due to large table size]...
Context: Table caption: Forbes Billionaires 2022.
Question: How many billionaires are there from the 'Technology' category?
Thought 1: I need to count the number of billionaires from the 'Technology' category.
Action 1: Operate[count the number of entries whose category is Technology ]
Observation 1: 343
Thought 2: In observation 1, 343 billionaires are from the 'Technology' category, therefore, the answer is 343.
Action 2: Finish[343]

Table: 
| VendorID | tpep_pickup_datetime | tpep_dropoff_datetime | passenger_count | trip_distance | RatecodeID | store_and_fwd_flag | PULocationID | DOLocationID | payment_type | fare_amount | extra | mta_tax | tip_amount | tolls_amount | improvement_surcharge | total_amount | congestion_surcharge | Airport_fee | airport_fee |
| 1 | 2023-02-01 00:32:53+00:00 | 2023-02-01 00:34:34+00:00 | 2 | 0.3 | 1 | N | 142 | 163 | 2 | 4.4 | 3.5 | 0.5 | 0.0 | 0 | 1.0 | 9.4 | 2.5 | 0.0 | nan |
| 2 | 2023-02-01 00:12:28+00:00 | 2023-02-01 00:25:46+00:00 | 1 | 3.22 | 1 | N | 161 | 145 | 1 | 17.0 | 1.0 | 0.5 | 3.3 | 0 | 1.0 | 25.3 | 2.5 | 0.0 | nan |
...[remaining 99997 rows unshown due to large table size]...
Context: Table caption: NYC Taxi Trips.
Question: Are there any trips with a total distance greater than 30 miles?
Thought 1: I need to count the number of entries whose 'trip_distance' is larger than 30.
Action 1: Operate[count the number of entries whose 'trip_distance' is larger than 30.]
Observation 1: 0
Thought 2: In observation 1, there is 0 entry whose trip distance is larger than 30. Therefore, the answer is False.
Action 2: Finish[False]

Table: 
| segmentation_1 | descriptor | complaint_type | created_date | borough | hour | month_name | weekday_name | agency | resolution_description | agency_name | city | location_type | incident_zip | incident_address | street_name | cross_street_1 | cross_street_2 | intersection_street_1 | intersection_street_2 | address_type | landmark | facility_type | status | due_date | resolution_action_updated_date | community_board | x_coordinate | y_coordinate | park_facility_name | park_borough | bbl | open_data_channel_type | vehicle_type | taxi_company_borough | taxi_pickup_location | bridge_highway_name | bridge_highway_direction | road_ramp | bridge_highway_segment | latitude | longitude | location | unique_key | Unnamed_0 | closed_date |
| [] | Business not in compliance | NonCompliance with Phased Reopening | 2020-10-02 11:50:27+00:00 | BROOKLYN | 11 | October | Friday | MAYORâ€™S OFFICE OF SPECIAL ENFORCEMENT | The complaint does not contains sufficient information to allow an inspection for this complaint type. | Mayorâ€™s Office of Special Enforcement | BROOKLYN | Store/Commercial | 11206.0 | 264 LEE AVENUE | LEE AVENUE | WALLABOUT STREET | FLUSHING AVENUE | WALLABOUT STREET | FLUSHING AVENUE | nan | LEE AVENUE | nan | Closed | NaT | 2020-10-24 10:37:51+00:00 | 01 BROOKLYN | 997089.0 | 194272.0 | Unspecified | BROOKLYN | 3022630048.0 | ONLINE | nan | nan | nan | nan | nan | nan | nan | 40.69989921811644 | -73.95369683046549 | [ 40.69989922 -73.95369683] | 47755664 | 0 | 2020-10-24 10:37:53+00:00 |
| [] | ENTIRE BUILDING | HEAT/HOT WATER | 2021-11-03 06:48:49+00:00 | QUEENS | 6 | November | Wednesday | HPD | The Department of Housing Preservation and Development contacted an occupant of the apartment and verified that the following conditions were corrected. The complaint has been closed. If the condition still exists, please file a new complaint. | Department of Housing Preservation and Development | JAMAICA | RESIDENTIAL BUILDING | 11432.0 | 170-03 JAMAICA AVENUE | JAMAICA AVENUE | nan | nan | nan | nan | ADDRESS | nan | nan | Closed | NaT | 2021-11-03 00:00:00+00:00 | 12 QUEENS | 1042627.0 | 197022.0 | Unspecified | QUEENS | 4098040010.0 | PHONE | nan | nan | nan | nan | nan | nan | nan | 40.707264062355954 | -73.78944300605664 | [ 40.70726406 -73.78944301] | 52403931 | 1 | 2021-11-03 12:53:46+00:00 |
Context: Table caption: NYC 311 Calls
Question: Mention the 2 most common weekdays for complaints.
Thought 1: I need to count the frequency of each unique weekday in the column 'weekday_name'.
Action 1: Operate[count the frequency of each unique weekday in the column 'weekday_name'.]
Observation 1: {'Tuesday': 15847, 'Monday': 15816, 'Wednesday': 15445, 'Thursday': 14978, 'Friday': 14707, 'Saturday': 11781, 'Sunday': 11426}
Thought 2: The question ask for 2 most common weekdays. From observation 1, we find Tuesday and Monday have the largest frequencies and they are weekdays. Therefore, the answer is ["Tuesday", "Monday"]
Action 2: Finish[["Tuesday", "Monday"]]
"""


TABLE_OPERATION_EXAMPLE = """
Instruction: extract the score of the game between the teams on 6 February 1922.
Table dateframe code: import pandas as pd
data={"Tie no": ["1", "2", "3", "Replay", "4", "5", "6", "7", "8", "9", "10", "11", "Replay", "12", "Replay", "13", "Replay", "Replay", "14", "Replay", "15", "16"], "Home team": ["Liverpool", "Preston North End", "Southampton", "Cardiff City", "Leicester City", "Nottingham Forest", "Aston Villa", "Bolton Wanderers", "Swindon Town", "Tottenham Hotspur", "Barnsley", "Northampton Town", "Stoke", "Brighton & Hove Albion", "Huddersfield Town", "Bradford City", "Notts County", "Notts County", "Crystal Palace", "Millwall", "Southend United", "Bradford Park Avenue"], "Score": ["0\u20131", "3\u20131", "1\u20131", "2\u20130", "2\u20130", "3\u20130", "1\u20130", "1\u20133", "0\u20131", "1\u20130", "3\u20131", "2\u20132", "3\u20130", "0\u20130", "2\u20130", "1\u20131", "0\u20130", "1\u20130", "0\u20130", "2\u20130", "0\u20131", "2\u20133"], "Away team": ["West Bromwich Albion", "Newcastle United", "Cardiff City", "Southampton", "Fulham", "Hull City", "Luton Town", "Manchester City", "Blackburn Rovers", "Watford", "Oldham Athletic", "Stoke", "Northampton Town", "Huddersfield Town", "Brighton & Hove Albion", "Notts County", "Bradford City", "Bradford City", "Millwall", "Crystal Palace", "Swansea Town", "Arsenal"], "Date": ["28 January 1922", "28 January 1922", "28 January 1922", "1 February 1922", "28 January 1922", "28 January 1922", "28 January 1922", "28 January 1922", "28 January 1922", "28 January 1922", "28 January 1922", "28 January 1922", "1 February 1922", "28 January 1922", "1 February 1922", "28 January 1922", "1 February 1922", "6 February 1922", "28 January 1922", "1 February 1922", "28 January 1922", "28 January 1922"]}
df=pd.DataFrame(data)
Code: ```Python
# Filter based on the date
filtered_df = df[df['Date'] == '6 February 1922']

# Rename the dataframe
new_table = filtered_df
```

Instruction: retrieve the number of passengers for Los Angeles and Saskatoon from the table in 2013.
Table dataframe code: import pandas as pd
data={"Rank": ["1", "2", "3", "4", "5", "6", "7", "8", "9"], "City": ["United States, Los Angeles", "United States, Houston", "Canada, Calgary", "Canada, Saskatoon", "Canada, Vancouver", "United States, Phoenix", "Canada, Toronto", "Canada, Edmonton", "United States, Oakland"], "Passengers": ["14,749", "5,465", "3,761", "2,282", "2,103", "1,829", "1,202", "110", "107"], "Ranking": ["", "", "", "4", "", "1", "1", "", ""], "Airline": ["Alaska Airlines", "United Express", "Air Transat, WestJet", "", "Air Transat", "US Airways", "Air Transat, CanJet", "", ""]}
df=pd.DataFrame(data)
Code: ```Python
# Filter the rows for Los Angeles and Saskatoon in 2013
filter_la = (df['City'] == 'United States, Los Angeles') & (df['Rank'] == '1')
filter_sask = (df['City'] == 'Canada, Saskatoon') & (df['Rank'] == '4')

# Apply the filter and store the result in 'new_table'
new_table = df.loc[filter_la | filter_sask, ['City', 'Passengers']]

# Rename the columns as required
new_table.columns = ['City', 'Passengers_2013']
```
"""


NUMERICAL_OPERATION_EXAMPLE = """
Instruction: count how many buildings have a height under 200 ft.
Dataframe code: import pandas as pd
data={"Rank": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21=", "21=", "23", "24", "25", "26", "27", "28", "29", "30"], "Name": ["Rhodes State Office Tower", "LeVeque Tower", "William Green Building", "Huntington Center", "Vern Riffe State Office Tower", "One Nationwide Plaza", "Franklin County Courthouse", "AEP Building", "Borden Building", "Three Nationwide Plaza", "One Columbus Center", "Columbus Center", "Capitol Square", "Continental Center", "PNC Bank Building", "Miranova Condominiums", "Fifth Third Center", "Motorists Mutual Building", "Midland Building", "The Condominiums at North Bank Park", "Lincoln Tower Dormitory", "Morrill Tower Dormitory", "Hyatt Regency Columbus", "Key Bank Building", "Adam's Mark Hotel", "Town Center", "8 East Broad Street", "Huntington Building", "Ohio Judicial Center", "16 East Broad Street"], "Height\\nft / m": ["629 / 192", "555 / 169", "530 / 162", "512 / 156", "503 / 153", "485 / 148", "464 / 141", "456 / 139", "438 / 134", "408 / 124", "366 / 112", "357 / 109", "350 / 107", "348 / 106", "317 / 97", "314 / 96", "302 / 92", "286 / 87", "280 / 85", "267 / 81", "260 / 79", "260 / 79", "256 / 78", "253 / 77", "243 / 74", "226 / 69", "212 / 64.6", "202 / 59.4", "200 / 57.9", "180 / 64.4"], "Floors": ["41", "47", "33", "37", "32", "40", "27", "31", "34", "27", "26", "25", "26", "26", "25", "26", "25", "21", "21", "20", "26", "26", "20", "20", "16", "17", "17", "13", "14", "13"], "Year": ["1973", "1927", "1990", "1984", "1988", "1976", "1991", "1983", "1974", "1989", "1987", "1964", "1984", "1973", "1977", "2001", "1998", "1973", "1970", "2007", "1967", "1967", "1980", "1963", "1961", "1974", "1906", "1926", "1933", "1900"], "Notes": ["Has been the tallest building in Columbus and the tallest mid-block skyscraper in Ohio since 1973. Tallest building constructed in Columbus in the 1970s.", "Tallest building constructed in Columbus in the 1920s.", "Tallest building constructed in Columbus in the 1990s.", "Tallest building constructed in Columbus in the 1980s.", "", "", "", "", "", "", "", "Tallest building constructed in Columbus in the 1960s. Was built as the Bank One Tower.", "", "", "", "Tallest residential building in the state of Ohio. Tallest building built in the 2000s.", "", "", "", "", "", "", "", "", "", "", "", "", "", ""]}
df=pd.DataFrame(data)
Information: 
Code: ```Python
# Conversion of height from string to numeric
df['Height'] = df['Height\\nft / m'].str.split(' / ').str[0].astype(int)

# Filter buildings with height under 200 ft
buildings_under_200ft = df[df['Height'] < 200]

# Counting the number of buildings
final_result = len(buildings_under_200ft)
```

Instruction: calculate the average of gold medals for the top 5 nations.
Dataframe code: import pandas as pd
data={"Rank": ["1", "2", "3", "4", "5"], "Nation": ["United States", "Jamaica", "Netherlands", "Bahamas", "Ukraine"], "Gold": ["5", "4", "2", "1", "1"], "Silver": ["6", "1", "0", "1", "0"], "Bronze": ["5", "1", "0", "0", "1"], "Total": ["16", "6", "2", "2", "2"]}
df=pd.DataFrame(data)
Code: ```Python
top_5_medals = df.["Gold"].astype(int).sum()
final_result = top_5_medals / 5
```
"""


NUMERICAL_OPERATION_EXAMPLE_LONG_TABLE = """
Instruction: count the number of entries whose category is Technology
Dataframe code for the first two records: import pandas as pd
data={'rank':[1.0, 2.0],'personName':['Elon Musk', 'Jeff Bezos'],'age':[50.0, 58.0],'finalWorth':[219000.0, 171000.0],'category':['Automotive', 'Technology'],'source':['Tesla, SpaceX', 'Amazon'],'country':['United States', 'United States'],'state':['Texas', 'Washington'],'city':['Austin', 'Seattle'],'organization':['Tesla', 'Amazon'],'selfMade':[1.0, 1.0],'gender':['M', 'M'],'birthDate':[Timestamp('1971-06-28 00:00:00+0000', tz='UTC'), Timestamp('1964-01-12 00:00:00+0000', tz='UTC')],'title':['CEO', 'Entrepreneur'],'philanthropyScore':[1.0, 1.0],'bio':["Elon Musk is working to revolutionize transportation both on Earth, through electric car maker Tesla -- and in space, via rocket producer SpaceX. He owns 21% of Tesla but has pledged more than half his stake as collateral for loans; Forbes has discounted his stake to take the loans into account. A regulatory filing in early April 2022 revealed that Musk had purchased 9.2% of Twitter. The company invited him to join its board the next day.   SpaceX, Musk's rocket company, is valued at $74 billion after a funding round in February 2021. He grew up in South Africa, then immigrated to Canada at age 17. He landed in the U.S. as a transfer student to the University of Pennsylvania.", 'Jeff Bezos founded e-commerce giant Amazon in 1994 out of his garage in Seattle. He stepped down as CEO to become executive chairman in July 2021. Bezos sold $8.8 billion worth of his Amazon stock in 2021 and also gave some shares away; he now owns a bit less than 10% of the company.  Amazon faced criticism from U.S. senators and the general public for its treatment of warehouse workers during the coronavirus pandemic. He and his wife MacKenzie divorced in 2019 after 25 years of marriage and he transferred a quarter of his then-16% Amazon stake to her. Bezos owns The Washington Post and Blue Origin, an aerospace company developing rockets; he briefly flew to space in one in July 2021.'],'about':['Musk was accepted to a graduate program at Stanford, but deferred attendance to launch his first business, software company Zip2. As a kid Musk taught himself to code; he sold his first game, Blastar, for about $500. ', "Growing up, Jeff Bezos worked summers on his grandfather's ranch repairing Caterpillar tractors. Bezos met Google founders Larry Page and Sergey Brin in 1998 and managed to become one of the company's first angel investors, putting in an estimated $250,000."]}
df=pd.DataFrame(data)
Information: 
Code: ```Python
# Define the function to count entries with category "Technology"  
def target_function(dataframe):  
    technology_entries_count = len(dataframe[dataframe['category'] == 'Technology])
    return technology_entries_count
```

Instruction: calculate the average of gold medals for the top 5 nations.
Dataframe code for the first two records: import pandas as pd
data={"Rank": ["1", "2"], "Nation": ["United States", "Jamaica"], "Gold": ["5", "4"], "Silver": ["6", "1"], "Bronze": ["5", "1"], "Total": ["16", "6"]}
df=pd.DataFrame(data)
Code: ```Python
# average number of gold medals for the top 5 nations in the dataframe
def target_function(dataframe):
    top_5_medals = dataframe["Gold"].astype(int).tolist()[:5]
    final_result = sum(top_5_medals) / 5
    return final_result
```
"""

GLOBAL_PLAN_EXAMPLES = """
Table: 
| rank | personName | age | finalWorth | category | source | country | state | city | organization | selfMade | gender | birthDate | title | philanthropyScore | bio | about |
| 1 | Elon Musk | 50.0 | 219000 | Automotive | Tesla, SpaceX | United States | Texas | Austin | Tesla | True | M | 1971-06-28 00:00:00+00:00 | CEO | 1.0 | Elon Musk is working to revolutionize transportation both on Earth, through electric car maker Tesla -- and in space, via rocket producer SpaceX. He owns 21% of Tesla but has pledged more than half his stake as collateral for loans; Forbes has discounted his stake to take the loans into account. A regulatory filing in early April 2022 revealed that Musk had purchased 9.2% of Twitter. The company invited him to join its board the next day.   SpaceX, Musk's rocket company, is valued at $74 billion after a funding round in February 2021. He grew up in South Africa, then immigrated to Canada at age 17. He landed in the U.S. as a transfer student to the University of Pennsylvania. | Musk was accepted to a graduate program at Stanford, but deferred attendance to launch his first business, software company Zip2. As a kid Musk taught himself to code; he sold his first game, Blastar, for about $500.  |
| 2 | Jeff Bezos | 58.0 | 171000 | Technology | Amazon | United States | Washington | Seattle | Amazon | True | M | 1964-01-12 00:00:00+00:00 | Entrepreneur | 1.0 | Jeff Bezos founded e-commerce giant Amazon in 1994 out of his garage in Seattle. He stepped down as CEO to become executive chairman in July 2021. Bezos sold $8.8 billion worth of his Amazon stock in 2021 and also gave some shares away; he now owns a bit less than 10% of the company.  Amazon faced criticism from U.S. senators and the general public for its treatment of warehouse workers during the coronavirus pandemic. He and his wife MacKenzie divorced in 2019 after 25 years of marriage and he transferred a quarter of his then-16% Amazon stake to her. Bezos owns The Washington Post and Blue Origin, an aerospace company developing rockets; he briefly flew to space in one in July 2021. | Growing up, Jeff Bezos worked summers on his grandfather's ranch repairing Caterpillar tractors. Bezos met Google founders Larry Page and Sergey Brin in 1998 and managed to become one of the company's first angel investors, putting in an estimated $250,000. |
...[remaining 2665 rows unshown due to large table size]...
Context: Table caption: Forbes Billionaires 2022.
Question: How many billionaires are there from the 'Technology' category?
Plan: 1. I need to filter the table to get all billionaires from the 'Technology' category.
2: Then I need to count the number of retrieved entries.
3. The answer to the question is the number of retrieved entries in the second step, and I will return this value as the final answer.

Table: 
| segmentation_1 | descriptor | complaint_type | created_date | borough | hour | month_name | weekday_name | agency | resolution_description | agency_name | city | location_type | incident_zip | incident_address | street_name | cross_street_1 | cross_street_2 | intersection_street_1 | intersection_street_2 | address_type | landmark | facility_type | status | due_date | resolution_action_updated_date | community_board | x_coordinate | y_coordinate | park_facility_name | park_borough | bbl | open_data_channel_type | vehicle_type | taxi_company_borough | taxi_pickup_location | bridge_highway_name | bridge_highway_direction | road_ramp | bridge_highway_segment | latitude | longitude | location | unique_key | Unnamed_0 | closed_date |
| [] | Business not in compliance | NonCompliance with Phased Reopening | 2020-10-02 11:50:27+00:00 | BROOKLYN | 11 | October | Friday | MAYORâ€™S OFFICE OF SPECIAL ENFORCEMENT | The complaint does not contains sufficient information to allow an inspection for this complaint type. | Mayorâ€™s Office of Special Enforcement | BROOKLYN | Store/Commercial | 11206.0 | 264 LEE AVENUE | LEE AVENUE | WALLABOUT STREET | FLUSHING AVENUE | WALLABOUT STREET | FLUSHING AVENUE | nan | LEE AVENUE | nan | Closed | NaT | 2020-10-24 10:37:51+00:00 | 01 BROOKLYN | 997089.0 | 194272.0 | Unspecified | BROOKLYN | 3022630048.0 | ONLINE | nan | nan | nan | nan | nan | nan | nan | 40.69989921811644 | -73.95369683046549 | [ 40.69989922 -73.95369683] | 47755664 | 0 | 2020-10-24 10:37:53+00:00 |
| [] | ENTIRE BUILDING | HEAT/HOT WATER | 2021-11-03 06:48:49+00:00 | QUEENS | 6 | November | Wednesday | HPD | The Department of Housing Preservation and Development contacted an occupant of the apartment and verified that the following conditions were corrected. The complaint has been closed. If the condition still exists, please file a new complaint. | Department of Housing Preservation and Development | JAMAICA | RESIDENTIAL BUILDING | 11432.0 | 170-03 JAMAICA AVENUE | JAMAICA AVENUE | nan | nan | nan | nan | ADDRESS | nan | nan | Closed | NaT | 2021-11-03 00:00:00+00:00 | 12 QUEENS | 1042627.0 | 197022.0 | Unspecified | QUEENS | 4098040010.0 | PHONE | nan | nan | nan | nan | nan | nan | nan | 40.707264062355954 | -73.78944300605664 | [ 40.70726406 -73.78944301] | 52403931 | 1 | 2021-11-03 12:53:46+00:00 |
Context: Table caption: NYC 311 Calls
Question: Mention the 2 most common weekdays for complaints.
Plan: 1. I need to count the frequency of each unique weekday in the column 'weekday_name'.
2. I will create an additional dataframe with two columns to store the results from the first step, with one column being the name of the weekday, and one being the frequency of that weekday.
3. I will sort the dataframe I made in step 2 in descending order.
4. The question asks 2 most common weekdays, this corresponds to the weekday values of the top two rows. I will retrieve the weekday values of the top two rows, store them in a list and return the list as the answer.
 
Table: 
| VendorID | tpep_pickup_datetime | tpep_dropoff_datetime | passenger_count | trip_distance | RatecodeID | store_and_fwd_flag | PULocationID | DOLocationID | payment_type | fare_amount | extra | mta_tax | tip_amount | tolls_amount | improvement_surcharge | total_amount | congestion_surcharge | Airport_fee | airport_fee |
| 1 | 2023-02-01 00:32:53+00:00 | 2023-02-01 00:34:34+00:00 | 2 | 0.3 | 1 | N | 142 | 163 | 2 | 4.4 | 3.5 | 0.5 | 0.0 | 0 | 1.0 | 9.4 | 2.5 | 0.0 | nan |
| 2 | 2023-02-01 00:12:28+00:00 | 2023-02-01 00:25:46+00:00 | 1 | 3.22 | 1 | N | 161 | 145 | 1 | 17.0 | 1.0 | 0.5 | 3.3 | 0 | 1.0 | 25.3 | 2.5 | 0.0 | nan |
...[remaining 99997 rows unshown due to large table size]...
Context: Table caption: NYC Taxi Trips.
Question: Are there any trips with a total distance greater than 30 miles?
Plan: 1. I need to count the number of entries whose 'trip_distance' is larger than 30.
2. If the value from step 1 is larger than 0, then the answer is 'True', otherwise, it is 'False'.
3. I will create a variable name after 'final_result' to store the boolean answer and return the variable as final answer.

"""

NUMERICAL_OPERATION_EXAMPLE_LONG_TABLE_GLOBAL = """
Plan: 1. I need to filter the table to get all billionaires from the 'Technology' category.
2: Then I need to count the number of retrieved entries.
3. The answer to the question is the number of retrieved entries in the second step, and I will return this value as the final answer.
Dataframe code for the first two records: import pandas as pd
data={'rank':[1.0, 2.0],'personName':['Elon Musk', 'Jeff Bezos'],'age':[50.0, 58.0],'finalWorth':[219000.0, 171000.0],'category':['Automotive', 'Technology'],'source':['Tesla, SpaceX', 'Amazon'],'country':['United States', 'United States'],'state':['Texas', 'Washington'],'city':['Austin', 'Seattle'],'organization':['Tesla', 'Amazon'],'selfMade':[1.0, 1.0],'gender':['M', 'M'],'birthDate':[Timestamp('1971-06-28 00:00:00+0000', tz='UTC'), Timestamp('1964-01-12 00:00:00+0000', tz='UTC')],'title':['CEO', 'Entrepreneur'],'philanthropyScore':[1.0, 1.0],'bio':["Elon Musk is working to revolutionize transportation both on Earth, through electric car maker Tesla -- and in space, via rocket producer SpaceX. He owns 21% of Tesla but has pledged more than half his stake as collateral for loans; Forbes has discounted his stake to take the loans into account. A regulatory filing in early April 2022 revealed that Musk had purchased 9.2% of Twitter. The company invited him to join its board the next day.   SpaceX, Musk's rocket company, is valued at $74 billion after a funding round in February 2021. He grew up in South Africa, then immigrated to Canada at age 17. He landed in the U.S. as a transfer student to the University of Pennsylvania.", 'Jeff Bezos founded e-commerce giant Amazon in 1994 out of his garage in Seattle. He stepped down as CEO to become executive chairman in July 2021. Bezos sold $8.8 billion worth of his Amazon stock in 2021 and also gave some shares away; he now owns a bit less than 10% of the company.  Amazon faced criticism from U.S. senators and the general public for its treatment of warehouse workers during the coronavirus pandemic. He and his wife MacKenzie divorced in 2019 after 25 years of marriage and he transferred a quarter of his then-16% Amazon stake to her. Bezos owns The Washington Post and Blue Origin, an aerospace company developing rockets; he briefly flew to space in one in July 2021.'],'about':['Musk was accepted to a graduate program at Stanford, but deferred attendance to launch his first business, software company Zip2. As a kid Musk taught himself to code; he sold his first game, Blastar, for about $500. ', "Growing up, Jeff Bezos worked summers on his grandfather's ranch repairing Caterpillar tractors. Bezos met Google founders Larry Page and Sergey Brin in 1998 and managed to become one of the company's first angel investors, putting in an estimated $250,000."]}
df=pd.DataFrame(data)
Code: ```Python 
def target_function(dataframe):  
    # filter the table for 'Technology' as the category and count the number of the entries
    technology_entries_count = len(dataframe[dataframe['category'] == 'Technology])
    # return the result as final answer
    return technology_entries_count
```

Plan: 1. I need to retrieve the first five values from the 'Gold' columns.
2. To calculate the average number, I will sum the retrieved values and divide the sum by 5.
3. The answer to the question is the result from step 2. I will return that value as the final answer.
Dataframe code for the first two records: import pandas as pd
data={"Rank": ["1", "2"], "Nation": ["United States", "Jamaica"], "Gold": ["5", "4"], "Silver": ["6", "1"], "Bronze": ["5", "1"], "Total": ["16", "6"]}
df=pd.DataFrame(data)
Code: ```Python
def target_function(dataframe):
    # retrieve the top 5 gold medals values from the table
    top_5_medals = dataframe["Gold"].astype(int).tolist()[:5]
    # get the average number of the gold medal
    final_result = sum(top_5_medals) / 5
    # return the result
    return final_result
```

"""
