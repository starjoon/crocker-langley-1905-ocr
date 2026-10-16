# Table formats

`scripts/parse_pages.py` turns the Sonnet transcripts into the CSV files below,
written to `data/tables/`. Every row keeps its source (`page_id`, `printed_page`,
`column`, `seq`) and the original text (`raw_text`), so any parsed field can be
checked against the page or re-parsed later without new OCR.

Parsed fields keep the directory's own spelling and abbreviations (`clk`, `r`,
`Devisadero`). Expanding abbreviations and standardizing addresses are later steps
that add new columns; they never overwrite these.

## pages.csv — one row per page

| Column | Example | Notes |
|---|---|---|
| page_id | crockerlangleysa1905sanf_0114 | scan file name |
| printed_page | 168 | from the running header; blank if unreadable |
| page_type | alphabetical | `alphabetical`, `street_guide`, or `classified` |
| header_raw | 168 ADA CROCKER-LANGLEY DIRECTORY. ADE | as transcribed |
| range_start, range_end | ADA, ADE | alphabetical pages: first letters of the first and last name |
| section_left, section_right | Art Societies, Banks | classified pages: sections on the page |
| transcribed_by | claude-haiku-5-5 | model whose transcript the row was parsed from |

## directory_entries.csv — alphabetical pages (most of the book)

One row per listing. A listing that wraps over several printed lines is one row.

| Column | Example | Notes |
|---|---|---|
| entry_id | 0114-c1-007 | page, column, order within the column |
| entry_type | person | `person`, `organization`, or `cross_reference` |
| bold | true | bold (paid) listing |
| name_raw | Adams William F | name as printed |
| surname | Adams | persons only; capitals normalized (`ADAMS` → `Adams`) |
| given_names | William F | persons only |
| name_title | Mrs | `Mrs`, `Miss`, `Dr`, `Rev`, … |
| name_suffix | Jr | `Jr`, `Sr`, … |
| is_widow | false | listed as `widow` |
| affiliation | Addotto & Bona | text in parentheses after the name (firm, partners, notes) |
| role_raw | bkkpr Broadway Brewery | everything describing what they do |
| occupation | bkkpr | leading lowercase part of `role_raw` |
| employer | Broadway Brewery | the rest of `role_raw`, or a separate employer segment |
| business_address | 604 Montgomery | work address; may include a building (`Schmiedell Bldg, 604 Montgomery`) |
| residence | 882 Lombard | after `r`; can be a town (`Berkeley`) or hotel |
| phone | Main 953 | after `tel` |
| see_also | Adami | cross references: the spelling(s) to look under |
| continued | false | true if the listing starts on the previous page |
| warnings | role has capitalized start | parts the parser wasn't sure about |
| raw_text | Adams William F, bkkpr Broadway / Brewery, r 882 Lombard | printed lines joined with ` / ` |

## streets.csv — Street and Avenue Guide

One row per street.

| Column | Example | Notes |
|---|---|---|
| street_id | 0063-c2-003 | |
| street_name | Bay View | |
| qualifier | late Parnassus Av | text in parentheses after the name |
| former_name | Parnassus Av | from `late …` in the qualifier |
| current_name | Treat Av | from `now …` in the qualifier |
| district | Bernal Heights | when the qualifier names a tract or district |
| description | from W s Railroad Av at Eighteenth Av South | route description |
| see_reference | Thirty-ninth Av South | from `see …` |
| continued | false | table rows at the top of a page belonging to a street on the previous page |
| raw_text | | |

## street_crossings.csv — Street Guide cross-street tables

One row per cross street, giving the house numbers where it meets the street.

| Column | Example | Notes |
|---|---|---|
| street_id | 0063-c2-003 | links to streets.csv (blank if the street is on the previous page) |
| street_name | Bay | |
| seq | 4 | order in the table |
| cross_street | Stockton | |
| side_a_label, side_b_label | N, S | from the `Cross Sts.` header (`N. S.`, `W. E.`, …) |
| side_a_raw, side_b_raw | 200, 201 | as printed: a number, `(e)`, `(o)`, `(c)`, `—`, or blank |
| side_a_number, side_b_number | 200, 201 | numeric values only |

## organizations.csv and officers.csv — classified pages

Classified pages (Banks, Art Societies, …) are paragraphs of prose, so these are
extracted by Claude into a fixed format rather than by rules.

organizations.csv:

| Column | Example |
|---|---|
| org_id | 0026-012 |
| section | Banks |
| name | Columbian Banking Company |
| address | Claus Spreckels Building, corner of Market and Third streets |
| organized, incorporated | March 10, 1893 |
| financials | Capital stock, $1,000,000; amount subscribed, $300,000; … |
| see_reference | The United States National Bank |
| continued | true if the paragraph starts on the previous page |
| continues_next_page | true if the paragraph runs onto the next page |
| raw_text | the full paragraph |

officers.csv — one row per person named in an organization's entry:

| Column | Example |
|---|---|
| org_id | 0026-012 |
| person_name | Jas. H. Swift |
| role | President |
| role_organization | Bank of California — only when the role is at another organization (committee members) |
