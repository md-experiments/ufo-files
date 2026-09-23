from datetime import date

from ufo.sources.pursue import parse_csv, parse_us_date, parse_year

HEADER = "﻿Featured,Redaction,Release Date,Title,Type,Video Pairing,PDF Pairing,Description Blurb,DVIDS Video ID,Video Title,Agency,Incident Date,Incident Location,PDF | Image Link,Modal Image,Image Alt Text,Image VIRIN,,,\n"
ROWS = [
    'YES,,9/18/26,"DOW-UAP-D102, Project Blue Book File on Tremonton Film, Utah, 1952",PDF,DOW-UAP-PR159,DOW-UAP-D098 | DOW-UAP-D103,"Later assessments were more confident that the objects were seabirds.",,,Department of War,7/2/52,"Tremonton, Utah",https://www.war.gov/medialink/ufo/x/DOW-UAP-D102.pdf,https://www.war.gov/thumb.jpg,alt,2609,,,',
    ',TRUE,5/8/26,"DOW-UAP-PR034, Unresolved UAP Report, Greece, October 2023",VID,,DoW-UAP-D033,"CENTCOM submitted a report.",1006080,Video,Department of War,N/A,Greece,https://www.war.gov/medialink/ufo/release_1/dow-uap-d33.pdf,,,,,,',
    ',TRUE,5/8/26,FBI Photo A001,IMG,,"USPER Statement | ODNI-UAP-D001, USPER Narrative",An image.,1006111,,FBI,Late 2025,N/A,https://www.war.gov/medialink/ufo/release_1/fbi-photo-a1.png,,,,,,',
    ',,5/8/26,"FBI-UAP-D014, Flying Discs, 1947",PDF ,,,,,,FBI,1947,,https://www.war.gov/a.pdf,,,,,,',
    ',,5/8/26,"FBI-UAP-D014, Flying Discs part 2, 1947",PDF,,,,,,FBI,1947,,https://www.war.gov/b.pdf,,,,,,',
    ',,,"no release date",PDF,,,,,,FBI,,,https://www.war.gov/c.pdf,,,,,,',
]


def test_parse_dates():
    today = date(2026, 9, 23)
    assert parse_us_date("7/2/52", today) == date(1952, 7, 2)
    assert parse_us_date("9/8/21", today) == date(2021, 9, 8)
    assert parse_us_date("12/31/2023", today) == date(2023, 12, 31)
    assert parse_us_date("N/A", today) is None
    assert parse_us_date("2/30/20", today) is None
    assert parse_year("Late 2025") == 2025
    assert parse_year("October, 2023") == 2023
    assert parse_year("N/A", "Something, 1969") == 1969


def test_parse_csv():
    recs = parse_csv(HEADER + "\n".join(ROWS), today=date(2026, 9, 23))
    assert len(recs) == 5  # row without a release date is skipped
    by_id = {r.record_id: r for r in recs}

    d102 = by_id["DOW-UAP-D102"]
    assert d102.media_type == "pdf"
    assert d102.release_date == date(2026, 9, 18)
    assert d102.incident_date == date(1952, 7, 2) and d102.incident_year == 1952
    assert d102.incident_location == "Tremonton, Utah"
    assert d102.related_ids == ["DOW-UAP-D098", "DOW-UAP-D103", "DOW-UAP-PR159"]
    assert d102.featured and not d102.redacted
    assert d102.file_url.endswith("DOW-UAP-D102.pdf")

    vid = by_id["DOW-UAP-PR034"]
    assert vid.media_type == "video"
    assert vid.file_url == "https://www.dvidshub.net/video/1006080"  # not the paired PDF
    assert vid.related_ids == ["DOW-UAP-D033"]
    assert vid.incident_location == "Greece" and vid.incident_year == 2023

    img = by_id["FBI Photo A001"]
    assert img.media_type == "image" and img.incident_year == 2025 and img.incident_location is None

    # duplicate ids in the feed stay distinct records; "PDF " type is trimmed
    assert by_id["FBI-UAP-D014"].media_type == "pdf"
    assert "FBI-UAP-D014#2" in by_id


def test_row_hash_changes_with_content():
    a = parse_csv(HEADER + ROWS[0])[0]
    b = parse_csv(HEADER + ROWS[0].replace("seabirds", "balloons"))[0]
    assert a.row_hash != b.row_hash
    assert a.row_hash == parse_csv(HEADER + ROWS[0])[0].row_hash
