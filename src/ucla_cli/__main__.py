import html
import json
import re
import csv
import os
import click
import time
import random
from argparse import ArgumentParser

from bs4 import BeautifulSoup, NavigableString
from termcolor import cprint
import requests
from requests.exceptions import ConnectionError, Timeout, RequestException

from ucla_cli import query
from ucla_cli import extract
from ucla_cli.course_titles_view import course_titles_view
from ucla_cli.display.kv_sections import display_buildings
from ucla_cli.get_course_summary import get_course_summary as imported_get_course_summary
from ucla_cli.results import results
from ucla_cli.clean import clean_course_summary
from ucla_cli import section_details

def extract_location(soup):
    location_columns = soup.find_all(class_="locationColumn")
    if len(location_columns) > 1:
        p = location_columns[1].find("p")
        if p and p.button:
            return p.button.text.strip()
        elif p:
            return p.text.strip()
    return "N/A"

def extract_course_summary(soup):
    status_data = soup.find_all(class_="statusColumn")[1].find("p")
    section_id = None
    section_link = None
    try:
        section_divs = soup.find_all(class_="cls-section")
        click.echo(click.style(f"Found {len(section_divs)} cls-section divs", fg='blue'))
        for i, div in enumerate(section_divs):
            if i == 0:
                click.echo(click.style(f"First cls-section div HTML sample: {div.prettify()[:200]}...", fg='blue'))
            p_tag = div.find('p', class_="hide-small")
            if p_tag:
                link_tag = p_tag.find('a')
                if link_tag and 'href' in link_tag.attrs:
                    href = link_tag['href']
                    section_id = link_tag.text.strip()
                    if href.startswith('/'):
                        href = "https://sa.ucla.edu" + href
                    section_link = href
                    click.echo(click.style(f"Found section link: {section_id} -> {section_link}", fg='green'))
                    break
    except Exception as e:
        click.echo(click.style(f"Error extracting section link: {str(e)}", fg='red'))
    result = {
        "status": [x for x in status_data.contents if isinstance(x, NavigableString)],
        "waitlist": soup.find_all(class_="waitlistColumn")[1].find("p").contents[0],
        "day": soup.find_all(class_="dayColumn")[1].find("p").text,
        "time": [x for x in soup.find_all(class_="timeColumn")[1].find_all("p")[1].contents if isinstance(x, NavigableString)],
        "location": extract_location(soup),
        "units": soup.find_all(class_="unitsColumn")[1].find("p").contents[0],
        "instructor": soup.find_all(class_="instructorColumn")[1].find("p").contents[0],
        "section_id": section_id,
        "section_link": section_link
    }
    click.echo(click.style(f"Extracted section_id: {result['section_id']}", fg='cyan'))
    click.echo(click.style(f"Extracted section_link: {result['section_link']}", fg='cyan'))
    return result

def extract_section_links(soup):
    section_links = {}
    section_divs = soup.find_all(class_="cls-section")
    click.echo(click.style(f"Found {len(section_divs)} cls-section divs for link extraction", fg='blue'))
    for div in section_divs:
        div_id = div.get('id', '')
        if not div_id:
            continue
        parts = div_id.split('_')
        if len(parts) < 2:
            continue
        class_id = parts[0]
        p_tag = div.find('p', class_="hide-small")
        if not p_tag:
            continue
        link_tag = p_tag.find('a')
        if not link_tag or 'href' not in link_tag.attrs:
            continue
        href = link_tag['href']
        section_id_text = link_tag.text.strip()
        if href.startswith('/'):
            href = "https://sa.ucla.edu" + href
        section_links[class_id] = {
            'section_id': section_id_text,
            'section_link': href
        }
        click.echo(click.style(f"Mapped class ID {class_id} to section {section_id_text}", fg='green'))
    return section_links

def extract_course_data(soup):
    scripts = soup.find_all(string=re.compile("addCourse"))
    models = []
    for script in scripts:
        m = re.search(r"AddToCourseData\((.*),({.*})\)", script.string)
        if m:
            course_id_json = m.group(1)
            model_json = m.group(2)
            try:
                course_id = json.loads(course_id_json)
                model = json.loads(model_json)
                models.append((course_id, model))
            except json.JSONDecodeError as e:
                click.echo(click.style(f"Error decoding JSON in extract_course_data: {e}", fg='red'))
                click.echo(click.style(f"Problematic course_id_json: {course_id_json}", fg='red'))
                click.echo(click.style(f"Problematic model_json: {model_json}", fg='red'))
    return models

def save_to_csv(term, subject, subject_name, courses, csv_filename=None):
    if not csv_filename:
        subject_clean = "".join(c if c.isalnum() else "_" for c in subject.strip())
        csv_filename = f"{term}/{subject_clean}.csv"
    if not courses:
        click.echo(f"No courses to export to CSV file.")
        return
    if courses and len(courses) > 0:
        click.echo(click.style("First course data being exported:", fg='blue'))
        first_course = courses[0]
        click.echo(click.style(f"Number: {first_course.get('number', 'N/A')}", fg='blue'))
        click.echo(click.style(f"Name: {first_course.get('name', 'N/A')}", fg='blue'))
    headers = ["Subject", "Subject Name", "Number", "Name", "Section ID", "Section Link",
           "Status", "Waitlist", "Day", "Time", "Location", "Units", "Instructor",
           "Course Description", "Class Description Detail", "General Education GE", 
           "Writing II Requirement", "Diversity Info", "Class Notes"]
    courses_with_links = sum(1 for course in courses if course.get("data", {}).get("section_link"))
    click.echo(click.style(f"Found {courses_with_links} out of {len(courses)} courses with section links for CSV", fg='green'))
    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            for i, course in enumerate(courses):
                data = course.get("data", {})
                section_id = data.get("section_id", "")
                section_link = data.get("section_link", "")
                row = [
                    subject.strip(),
                    subject_name.strip(),
                    course.get("number", "").strip(),
                    course.get("name", "").strip(),
                    section_id,
                    section_link,
                    " ".join(str(x).strip() for x in data.get("status", [])),
                    data.get("waitlist", ""),
                    data.get("day", ""),
                    " ".join(str(x).strip() for x in data.get("time", [])),
                    data.get("location", ""),
                    data.get("units", ""),
                    data.get("instructor", ""),
                    data.get("course_description", ""),
                    data.get("class_description_detail", ""),
                    data.get("general_education_ge", ""),
                    data.get("writing_ii_requirement", ""),
                    data.get("diversity_info", ""),
                    data.get("class_notes", "")
                ]
                writer.writerow(row)
        click.echo(f"Courses exported to CSV file: {csv_filename}")
        with open(csv_filename, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            _ = next(reader)
            rows_read = list(reader)
            links_in_csv = sum(1 for row_data in rows_read if len(row_data) > 5 and row_data[5])
            click.echo(click.style(f"CSV validation: Found {links_in_csv} rows with section links in '{csv_filename}'", fg='green'))
    except Exception as e:
        click.echo(f"Error writing to CSV file: {e}")

def extract_all_section_data(soup):
    sections = []
    section_rows = soup.find_all("div", class_=lambda x: x and "data_row" in x and "primary-row" in x)
    click.echo(click.style(f"Found {len(section_rows)} section rows in course summary (extract_all_section_data)", fg='blue'))
    for row_idx, row in enumerate(section_rows):
        try:
            row_id = row.get('id', '')
            class_id = row_id.split('_')[0] if '_' in row_id else None
            status_col = row.find(class_="statusColumn")
            waitlist_col = row.find(class_="waitlistColumn")
            day_col = row.find(class_="dayColumn")
            time_col = row.find(class_="timeColumn")
            location_col = row.find(class_="locationColumn")
            units_col = row.find(class_="unitsColumn")
            instructor_col = row.find(class_="instructorColumn")
            section_id_text = None
            section_link_href = None
            first_col_content = row.find(class_=lambda x: x and ("sectionColumn" in x or "col-1" in x))
            if not first_col_content:
                 cols = row.find_all(lambda tag: tag.name == 'div' and 'Column' in ''.join(tag.get('class', [])))
                 if cols:
                    first_col_content = cols[0]
            if first_col_content:
                link_tag = first_col_content.find('a', href=True)
                if link_tag:
                    href_val = link_tag['href']
                    section_id_text = link_tag.text.strip()
                    if href_val.startswith('/'):
                        href_val = "https://sa.ucla.edu" + href_val
                    section_link_href = href_val
            time_p_tags = time_col.find_all("p") if time_col else []
            time_data = []
            if len(time_p_tags) > 1:
                 time_data = [x for x in time_p_tags[1].contents if isinstance(x, NavigableString)]
            elif time_p_tags:
                 time_data = [x for x in time_p_tags[0].contents if isinstance(x, NavigableString)]
            section_data = {
                "class_id": class_id,
                "status": [x for x in status_col.find("p").contents if isinstance(x, NavigableString)] if status_col and status_col.find("p") else ["N/A"],
                "waitlist": waitlist_col.find("p").contents[0].strip() if waitlist_col and waitlist_col.find("p") and waitlist_col.find("p").contents else "N/A",
                "day": day_col.find("p").text.strip() if day_col and day_col.find("p") else "N/A",
                "time": time_data,
                "location": extract_location(row) if location_col else "N/A",
                "units": units_col.find("p").contents[0].strip() if units_col and units_col.find("p") and units_col.find("p").contents else "N/A",
                "instructor": instructor_col.find("p").contents[0].strip() if instructor_col and instructor_col.find("p") and instructor_col.find("p").contents else "N/A",
                "section_id": section_id_text,
                "section_link": section_link_href
            }
            sections.append(section_data)
            click.echo(click.style(f"Extracted section data: {section_id_text} (Class ID: {class_id})", fg='green'))
        except Exception as e:
            click.echo(click.style(f"Error extracting one section's data (row {row_idx}): {str(e)}", fg='red'))
            sections.append({
                "class_id": None, "status": ["Error"], "waitlist": "Error", "day": "Error",
                "time": ["Error"], "location": "Error", "units": "Error", "instructor": "Error",
                "section_id": f"Error_Row_{row_idx}", "section_link": None
            })
    return sections

def get_course_summary_for_all_sections(model):
    sum_soup = imported_get_course_summary(model)
    if not sum_soup:
        click.echo(click.style(f"Failed to get course summary HTML for model: {model.get('classId', 'Unknown ID')}", fg='red'))
        return []
    sections = extract_all_section_data(sum_soup)
    return sections

def display_course(subject, subject_name, number, name, data, orig_data, course_details, section_label=None):
    course_title_str = f"{subject_name} ({subject}) {number} - {name}"
    if section_label:
        click.secho(f"\n--- {course_title_str} ({section_label}) ---", fg="cyan", bold=True)
    else:
        click.secho(f"\n--- {course_title_str} ---", fg="cyan", bold=True)
    if not course_details:
        click.echo(" (Run without --quiet for more details)")
        return
    if not data:
        click.echo("  No detailed section information available.")
        return
    
    display_order = [
        "status", "waitlist", "day", "time", "location", "units", "instructor", "section_id", "section_link",
        "course_description", "class_description_detail", "general_education_ge", 
        "writing_ii_requirement", "diversity_info", "class_notes"
    ]

    displayed_keys = set()

    for key in display_order:
        value = data.get(key)
        if value and value != "N/A" and value != "N/A (No URL provided)" and value != "N/A (Failed to fetch page content)" and value != "N/A (Empty)":
            if key in ["section_id", "section_link"]:
                click.secho(f"  {key.replace('_', ' ').title()}: ", fg="yellow", nl=False)
            else:
                click.secho(f"  {key.replace('_', ' ').title()}: ", fg="green", nl=False)
            
            if isinstance(value, list):
                click.secho(" ".join(map(str, value)).strip(), fg="white")
            else:
                click.secho(str(value).strip(), fg="white")
            displayed_keys.add(key)

    # Display any other keys not in display_order (if any)
    for key, value in data.items():
        if key not in displayed_keys and value and value != "N/A" and value != "N/A (No URL provided)" and value != "N/A (Failed to fetch page content)" and value != "N/A (Empty)":
            click.secho(f"  {key.replace('_', ' ').title()}: ", fg="blue", nl=False)
            if isinstance(value, list):
                click.secho(" ".join(map(str, value)).strip(), fg="white")
            else:
                click.secho(str(value).strip(), fg="white")

def soc(term, subject, course_details, mode, csv_export=False, quiet_csv=False):
    text = results()
    def reduce_subject(x):
        return x.replace(" ", "").lower()
    subject_table_search = re.search(r"SearchPanelSetup\('(\[.*\])'.*\)", text)
    # if not subject_table_search:
    #     click.echo(click.style("Could not find subject table in initial results.", fg='red'))
    #     return
    subject_table_json = html.unescape(subject_table_search.group(1))
    subject_table = json.loads(subject_table_json)
    subject_name_table = {reduce_subject(x["value"]): x["label"] for x in subject_table}
    subject_code_table = {reduce_subject(x["value"]): x["value"] for x in subject_table}
    reduced_subj = reduce_subject(subject)

    # click.echo(f"Subject table: {subject_name_table}")

    ## courses not included in summer 25
    if reduced_subj == "afrcst":
        subject_name = "African Studies"
        subject_code = "AFRC ST"
    # elif reduced_subj == "anes":
    #     subject_name = "Anesthesiology"
    #     subject_code = "ANES"
    elif reduced_subj == "appling":
        subject_name = "Applied Linguistics"
        subject_code = "APPLING"
    elif reduced_subj == "art&arc":
        subject_name = "Arts and Architecture"
        subject_code = "ART&ARC"
    elif reduced_subj not in subject_name_table:
        click.echo(click.style(f"Subject '{subject}' not found. Please use a valid subject area.", fg='red'))
        return
    else:
        subject_name = subject_name_table[reduced_subj]
        subject_code = subject_code_table[reduced_subj]
    text = results(term, subject_code)
    soup = BeautifulSoup(text, 'html.parser')
    locations_options = soup.select("#Location_options option")
    locations = {l.contents[0]: l['value'] for l in locations_options if l.contents}
    filters = {'location': locations}
    page = 1
    last_page = False
    all_courses_for_csv = []
    section_link_missing_count = 0
    
    # Dictionary to store course descriptions for special course numbers
    special_course_details = {}
    
    while not last_page:
        click.echo(f"Fetching page {page} for {subject_name}...")
        text_page = course_titles_view(term, subject_code, subject_name, page)
        if not text_page:
             click.echo(f"No more content found on page {page}. Assuming end of results.")
             break
        page += 1
        soup_page = BeautifulSoup(text_page, "html.parser")
        section_links_map = extract_section_links(soup_page)
        models = extract_course_data(soup_page)
        if not models:
            click.echo("No course models found on this page. Ending search.")
            break
        
        for course_id_str, model_data in models:
            title_element = soup_page.find(id=course_id_str + "-title")
            if not title_element or not title_element.contents:
                click.echo(click.style(f"Could not find title for course ID {course_id_str}", fg='yellow'))
                continue
            title_full = title_element.contents[0].strip()
            try:
                course_number, course_name_part = title_full.split(" - ", 1)
            except ValueError:
                click.echo(click.style(f"Could not parse title: {title_full}", fg='yellow'))
                course_number = title_full
                course_name_part = "N/A"
            course_number = course_number.strip()
            course_name_part = course_name_part.strip()
            
            # Check if this is a special course number (299, 596, 597, 598, 599)
            is_special_course = any(special_num in course_number for special_num in ['299', '596', '597', '598', '599'])
            
            if course_details:
                max_retries = 3
                retry_count = 0
                course_sections = []
                
                while retry_count < max_retries:
                    try:
                        time.sleep(random.uniform(0.5, 2.0))
                        course_sections = get_course_summary_for_all_sections(model_data)
                        break
                    except (ConnectionError, Timeout, RequestException) as e:
                        retry_count += 1
                        delay = retry_count * 2
                        click.echo(click.style(f"Connection error for {subject_code} {course_number}. Retry {retry_count}/{max_retries} in {delay}s: {str(e)}", fg='red'))
                        time.sleep(delay)
                        if retry_count >= max_retries:
                            click.echo(click.style(f"Failed to get course summary for {subject_code} {course_number} after {max_retries} retries.", fg='red'))
                            course_sections = [{
                                "status": ["Unknown - Connection Error"], "waitlist": "Unknown", "day": "Unknown",
                                "time": ["Unknown"], "location": "Unknown", "units": "Unknown",
                                "instructor": "Unknown", "section_id": None, "section_link": None, "class_id": model_data.get('classId')
                            }]
                
                if not course_sections:
                    course_sections = [{
                        "status": ["Unknown - Data Not Found"], "waitlist": "N/A", "day": "N/A",
                        "time": ["N/A"], "location": "N/A", "units": "N/A",
                        "instructor": "N/A", "section_id": None, "section_link": None, "class_id": model_data.get('classId')
                    }]
                
                # For special course numbers, we'll only fetch detailed info for the first section
                shared_details = {}
                
                for i, section_data_item in enumerate(course_sections): 
                    class_id_from_section = section_data_item.get('class_id')
                    if class_id_from_section and class_id_from_section in section_links_map:
                        if not section_data_item.get('section_id'):
                            section_data_item['section_id'] = section_links_map[class_id_from_section]['section_id']
                        if not section_data_item.get('section_link'):
                             section_data_item['section_link'] = section_links_map[class_id_from_section]['section_link']
                             click.echo(click.style(f"Used fallback section link for {subject_code} {course_number} section {section_data_item.get('section_id', 'Unknown')}", fg='magenta'))
                    
                    details_from_link = {}
                    
                    should_fetch_details = True
                    
                    if is_special_course:
                        if i == 0:
                            click.echo(click.style(f"Special course detected: {course_number}. Will fetch details only for first section.", fg='cyan'))
                        else:
                            should_fetch_details = False
                            click.echo(click.style(f"Using cached details for {course_number} section {section_data_item.get('section_id', f'#{i+1}')}", fg='cyan'))
                    
                    if not section_data_item.get("section_link"):
                        section_link_missing_count += 1
                        click.echo(click.style(f"Section link missing for: {subject_code} {course_number} - {course_name_part}, Section: {section_data_item.get('section_id', f'#{i+1}')}", fg='yellow'))
                    elif should_fetch_details:
                        details_from_link = section_details.extract_section_details_from_url(section_data_item["section_link"])
                        
                        if is_special_course and i == 0:
                            shared_details = {
                                "course_description": details_from_link.get("course_description"),
                                "class_description_detail": details_from_link.get("class_description_detail"),
                                "general_education_ge": details_from_link.get("general_education_ge"),
                                "writing_ii_requirement": details_from_link.get("writing_ii_requirement"),
                                "diversity_info": details_from_link.get("diversity_info"),
                                "class_notes": details_from_link.get("class_notes")
                            }
                    elif is_special_course and i > 0:
                        details_from_link = shared_details
                    
                    section_data_item.update(details_from_link)
                    
                    orig_section_data = section_data_item.copy()
                    cleaned_section_data = clean_course_summary(section_data_item, filters, mode)
                    
                    preserved_keys = [
                        "section_id", "section_link", "course_description", "class_description_detail", 
                        "general_education_ge", "writing_ii_requirement", "diversity_info", "class_notes"
                    ]
                    for key in preserved_keys:
                        if orig_section_data.get(key):
                            cleaned_section_data[key] = orig_section_data[key]
                        elif not cleaned_section_data.get(key) and details_from_link.get(key): # If clean_course_summary removed it, but details_from_link had it
                            cleaned_section_data[key] = details_from_link[key]

                    if csv_export:
                        all_courses_for_csv.append({
                            "number": course_number,
                            "name": course_name_part,
                            "data": cleaned_section_data
                        })
                    if not (csv_export and quiet_csv):
                        section_display_label = f"Section {cleaned_section_data.get('section_id', str(i+1))}" if len(course_sections) > 1 else None
                        display_course(subject_code, subject_name, course_number, course_name_part,
                                       cleaned_section_data, orig_section_data, course_details, section_display_label)
            else: 
                data_for_summary = {}
                orig_data_for_summary = {} 
                class_id_from_model = model_data.get('classId')
                current_section_link = None
                if class_id_from_model and class_id_from_model in section_links_map:
                    data_for_summary['section_id'] = section_links_map[class_id_from_model]['section_id']
                    current_section_link = section_links_map[class_id_from_model]['section_link']
                    data_for_summary['section_link'] = current_section_link
                
                if csv_export and current_section_link:
                    details_from_link = section_details.extract_section_details_from_url(current_section_link)
                    data_for_summary.update(details_from_link)

                if csv_export:
                    all_courses_for_csv.append({
                        "number": course_number,
                        "name": course_name_part,
                        "data": data_for_summary 
                    })
                if not (csv_export and quiet_csv):
                    display_course(subject_code, subject_name, course_number, course_name_part,
                                   data_for_summary, orig_data_for_summary, course_details, section_label=None)
        if soup_page.find(class_="lastPage"):
            last_page = True
            click.echo("Detected last page.")
    if section_link_missing_count > 0:
        click.echo(click.style(f"Total sections with missing links: {section_link_missing_count}", fg='yellow'))
    if csv_export and all_courses_for_csv:
        save_to_csv(term, subject_code, subject_name, all_courses_for_csv)

def bl():
    text = query.building_list()
    buildings = extract.building_list(text)
    display_buildings(buildings)

def cgs(term, building, room):
    if not building:
        bl()
    else:
        text = query.classroom_detail(term, building, room)
        data = extract.calendar_data(text)
        for x in data:
            print("{}-{}".format(x['strt_time'], x['stop_time']), x['title'])

@click.group()
def ucla():
    pass

@ucla.group(help="Search for classes offered in a term")
@click.argument("term")
@click.pass_context
@click.option("-q", "--quiet", is_flag=True, help="Just list course subject, name and title")
@click.option("-h", "--human-readable", is_flag=True, help="Output in plain, human-readable format")
def classes(ctx, term, quiet, human_readable):
    ctx.ensure_object(dict)
    ctx.obj['TERM'] = term
    ctx.obj['COURSE_DETAILS'] = not quiet
    ctx.obj['MODE'] = "plain" if human_readable else "hacker"

@classes.command()
@click.argument("subject-area", type=str, required=True)
@click.option("--csv", is_flag=True, help="Export results to a CSV file")
@click.option("--quiet-csv", is_flag=True, help="Suppress terminal output when exporting to CSV")
@click.pass_context
def subject_area(ctx, subject_area, csv, quiet_csv):
    soc(ctx.obj['TERM'], subject_area, ctx.obj['COURSE_DETAILS'], ctx.obj['MODE'], csv, quiet_csv)

@ucla.command()
@click.argument("term")
@click.option("-b", "--building", help="Building code")
@click.option("-r", "--room", help="Room number")
def rooms(term, building, room):
    cgs(term, building, room)

if __name__ == "__main__":
    ucla()