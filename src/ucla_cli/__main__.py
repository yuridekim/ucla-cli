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
from requests.exceptions import ConnectionError, Timeout

from ucla_cli import query
from ucla_cli import extract
from ucla_cli.course_titles_view import course_titles_view
from ucla_cli.display.kv_sections import display_buildings, display_course
from ucla_cli.get_course_summary import get_course_summary
from ucla_cli.results import results
from ucla_cli.clean import clean_course_summary

def extract_location(soup):
    p = soup.find_all(class_="locationColumn")[1].find("p")
    if p.button:
        return p.button.text.strip()
    return p.text.strip()

def extract_course_summary(soup):
    status_data = soup.find_all(class_="statusColumn")[1].find("p")
    
    # Extract section link
    section_id = None
    section_link = None
    try:
        # Look for the cls-section elements that contain section links
        section_divs = soup.find_all(class_="cls-section")
        click.echo(click.style(f"Found {len(section_divs)} cls-section divs", fg='blue'))
        
        for i, div in enumerate(section_divs):
            # Print a sample of the HTML to debug
            if i == 0:
                click.echo(click.style(f"First cls-section div HTML sample: {div.prettify()[:200]}...", fg='blue'))
            
            # Look for the <p> with class="hide-small" that contains the link
            p_tag = div.find('p', class_="hide-small")
            if p_tag:
                # Find the anchor tag within this paragraph
                link_tag = p_tag.find('a')
                if link_tag and 'href' in link_tag.attrs:
                    href = link_tag['href']
                    section_id = link_tag.text.strip()  # This gives us the section ID
                    
                    # Ensure we have a full URL
                    if href.startswith('/'):
                        href = "https://sa.ucla.edu" + href
                    
                    section_link = href
                    click.echo(click.style(f"Found section link: {section_id} -> {section_link}", fg='green'))
                    break  # Take the first one we find
    except Exception as e:
        click.echo(click.style(f"Error extracting section link: {str(e)}", fg='red'))
    
    # Debug the final extracted data
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
    
    # Print out section information for debugging
    click.echo(click.style(f"Extracted section_id: {result['section_id']}", fg='cyan'))
    click.echo(click.style(f"Extracted section_link: {result['section_link']}", fg='cyan'))
    
    return result

def extract_section_links(soup):
    """
    Extract all section links from the course list page.
    Returns a dictionary mapping class IDs to section info.
    """
    section_links = {}
    
    # Find all cls-section divs
    section_divs = soup.find_all(class_="cls-section")
    click.echo(click.style(f"Found {len(section_divs)} cls-section divs for link extraction", fg='blue'))
    
    for div in section_divs:
        # Extract section ID and class ID from the div
        div_id = div.get('id', '')
        if not div_id:
            continue
            
        # Parse div ID to get class ID - format is typically like "587992201_COMSCI0599-section"
        parts = div_id.split('_')
        if len(parts) < 2:
            continue
            
        class_id = parts[0]
        
        # Find the link inside this div
        p_tag = div.find('p', class_="hide-small")
        if not p_tag:
            continue
            
        link_tag = p_tag.find('a')
        if not link_tag or 'href' not in link_tag.attrs:
            continue
            
        href = link_tag['href']
        section_id = link_tag.text.strip()
        
        # Ensure full URL
        if href.startswith('/'):
            href = "https://sa.ucla.edu" + href
            
        # Store in our lookup dictionary using class ID as the key
        section_links[class_id] = {
            'section_id': section_id,
            'section_link': href
        }
        
        click.echo(click.style(f"Mapped class ID {class_id} to section {section_id}", fg='green'))
    
    return section_links

def extract_course_data(soup):
    scripts = soup.find_all(string=re.compile("addCourse"))
    models = []
    for script in scripts:
        m = re.search(r"AddToCourseData\((.*),({.*})\)", script.string)
        course_id = json.loads(m.group(1))
        model = json.loads(m.group(2))
        models.append((course_id, model))
    return models

# Function to save course data to CSV file
def save_to_csv(term, subject, subject_name, courses, csv_filename=None):
    """
    Save course data to a CSV file with detailed debugging.
    """
    if not csv_filename:
        subject_clean = subject.strip()
        csv_filename = f"{term}_{subject_clean}.csv"
    
    # Ensure we have at least one course
    if not courses:
        click.echo(f"No courses to export to CSV file.")
        return
    
    # Debug the first course data to see what's available
    if courses and len(courses) > 0:
        click.echo(click.style("First course data being exported:", fg='blue'))
        click.echo(click.style(f"Number: {courses[0]['number']}", fg='blue'))
        click.echo(click.style(f"Name: {courses[0]['name']}", fg='blue'))
        if courses[0]['data']:
            click.echo(click.style("Data keys:", fg='blue'))
            for key in courses[0]['data'].keys():
                click.echo(click.style(f"  - {key}: {courses[0]['data'].get(key)}", fg='blue'))
    
    # Manually define the headers
    headers = ["Subject", "Subject Name", "Number", "Name", "Section ID", "Section Link", 
               "Status", "Waitlist", "Day", "Time", "Location", "Units", "Instructor"]
    
    # Count how many courses have section links for reporting
    courses_with_links = sum(1 for course in courses if course.get("data", {}).get("section_link"))
    click.echo(click.style(f"Found {courses_with_links} out of {len(courses)} courses with section links", fg='green'))
    
    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            
            # Write each course as a row
            for i, course in enumerate(courses):
                data = course.get("data", {})
                
                # Get section_id and section_link directly from data
                section_id = data.get("section_id", "")
                section_link = data.get("section_link", "")
                
                # Debug for the first few rows
                if i < 3:  # Only show debug for first 3 rows
                    click.echo(click.style(f"Course {i+1} - Section ID: {section_id}", fg='cyan'))
                    click.echo(click.style(f"Course {i+1} - Section Link: {section_link}", fg='cyan'))
                
                # Build the row with specific field order
                row = [
                    subject.strip(),
                    subject_name.strip(),
                    course["number"].strip(),
                    course["name"].strip(),
                    section_id,
                    section_link,
                    " ".join(str(x).strip() for x in data.get("status", [])),
                    data.get("waitlist", ""),
                    data.get("day", ""),
                    " ".join(str(x).strip() for x in data.get("time", [])),
                    data.get("location", ""),
                    data.get("units", ""),
                    data.get("instructor", "")
                ]
                
                writer.writerow(row)
                
        click.echo(f"Courses exported to CSV file: {csv_filename}")
        
        # Quick validation of the CSV
        with open(csv_filename, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            headers = next(reader)  # Skip header row
            rows = list(reader)
            links_in_csv = sum(1 for row in rows if row[5])  # Section Link is column 5
            click.echo(click.style(f"CSV validation: Found {links_in_csv} rows with section links", fg='green'))
        
    except Exception as e:
        click.echo(f"Error writing to CSV file: {e}")

def soc(term, subject, course_details, mode, csv_export=False, quiet_csv=False):
    text = results()

    def reduce_subject(x):
        return x.replace(" ", "").lower()

    subject_table = re.search(r"SearchPanelSetup\('(\[.*\])'.*\)", text)
    subject_table = html.unescape(subject_table.group(1))
    subject_table = json.loads(subject_table)
    subject_name_table = {reduce_subject(x["value"]): x["label"] for x in subject_table}
    subject_code_table = {reduce_subject(x["value"]): x["value"] for x in subject_table}
    subject_name = subject_name_table[reduce_subject(subject)]
    subject = subject_code_table[reduce_subject(subject)]
    
    # we call results() again with our "main search field"
    # this is just to get the filter options, not the course list
    # but we call course_titles_view() for purely unfiltered course list
    text = results(term, subject)
    soup = BeautifulSoup(text, 'html.parser')
    locations = soup.select("#Location_options option")
    locations = {l.contents[0]: l['value'] for l in locations}
    filters = {
        'location': locations
    }
    
    page = 1
    last_page = False
    all_courses = []  # Store all courses for CSV export
    section_link_missing_count = 0  # Count courses with missing section links
    
    while not last_page:
        text = course_titles_view(term, subject, subject_name, page)
        last_page = False
        page += 1
        soup = BeautifulSoup(text, "html.parser")
        
        # Extract section links for all courses on this page
        section_links = extract_section_links(soup)
        
        models = extract_course_data(soup)
        if not models:
            break
            
        for course_id, model in models:
            title = soup.find(id=course_id + "-title").contents[0]
            number, name = title.split(" - ")
            
            if course_details:
                # Add retry mechanism for getting course summary
                max_retries = 3
                retry_count = 0
                sum_soup = None
                
                while retry_count < max_retries:
                    try:
                        # Add a delay to prevent overwhelming the server
                        time.sleep(random.uniform(0.5, 2.0))
                        
                        sum_soup = get_course_summary(model)
                        break  # Success, break the retry loop
                    except (ConnectionError, Timeout, requests.exceptions.RequestException) as e:
                        retry_count += 1
                        delay = retry_count * 2  # Exponential backoff
                        click.echo(click.style(f"Connection error for {subject} {number}. Retry {retry_count}/{max_retries} in {delay}s: {str(e)}", fg='red'))
                        time.sleep(delay)
                        
                        if retry_count >= max_retries:
                            click.echo(click.style(f"Failed to get course summary for {subject} {number} after {max_retries} retries.", fg='red'))
                            # Create empty data and continue to next course
                            data = {
                                "status": ["Unknown - Connection Error"],
                                "waitlist": "Unknown",
                                "day": "Unknown",
                                "time": ["Unknown"],
                                "location": "Unknown",
                                "units": "Unknown",
                                "instructor": "Unknown",
                                "section_id": None,
                                "section_link": None
                            }
                
                # If we got a successful response, extract the course summary
                if sum_soup:
                    try:
                        data = extract_course_summary(sum_soup)
                    except Exception as e:
                        click.echo(click.style(f"Error extracting course summary for {subject} {number}: {str(e)}", fg='red'))
                        # Create empty data if extraction fails
                        data = {
                            "status": ["Unknown - Extraction Error"],
                            "waitlist": "Unknown",
                            "day": "Unknown",
                            "time": ["Unknown"],
                            "location": "Unknown",
                            "units": "Unknown",
                            "instructor": "Unknown",
                            "section_id": None,
                            "section_link": None
                        }
                
                # Add section info from our section_links lookup
                class_id = model.get('classId')
                if class_id and class_id in section_links:
                    data['section_id'] = section_links[class_id]['section_id']
                    data['section_link'] = section_links[class_id]['section_link']
                    click.echo(click.style(f"Added section link for {subject} {number}: {data['section_id']} -> {data['section_link']}", fg='green'))
                
                # Check if section link is missing and report the specific course
                if not data.get("section_link"):
                    section_link_missing_count += 1
                    click.echo(click.style(f"Section link missing for course: {subject} {number} - {name}", fg='yellow'))
                
                # Store the original data before cleaning
                orig_data = data.copy()
                
                # Get a clean version for display, but preserve section info
                section_id = data.get('section_id')
                section_link = data.get('section_link')
                data = clean_course_summary(data, filters, mode)
                
                # Restore section info that might have been lost in cleaning
                if section_id:
                    data['section_id'] = section_id
                if section_link:
                    data['section_link'] = section_link
            else:
                data = {}
                orig_data = {}
                
            # Store for CSV export if requested
            if csv_export:
                all_courses.append({
                    "number": number,
                    "name": name,
                    "data": data
                })
            
            # Only display in terminal if not in quiet CSV mode
            if not (csv_export and quiet_csv):
                display_course(subject, subject_name, number, name, data, orig_data, course_details)
    
    # Report total number of missing section links
    if section_link_missing_count > 0:
        click.echo(click.style(f"Total courses with missing section links: {section_link_missing_count}", fg='yellow'))
        
    # Export to CSV if requested
    if csv_export and all_courses:
        save_to_csv(term, subject, subject_name, all_courses)


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
# @click.argument("search-criteria", type=click.Choice(["subject-area", "class-units", "class-id", "instructor", "general-education", 
#                                                       "writing-2", "diversity", "college-honors", "fiat-lux", "community-engaged-learning", 
#                                                       "law", "online-not-recorded", "online-recorded", "online-asynchronous",]))
@click.option("-q", "--quiet", is_flag=True, help="Just list course subject, name and title")
@click.option("-h", "--human-readable", is_flag=True)
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

# New test command
@ucla.command(help="Test if the CLI is working properly")
@click.option("-v", "--verbose", is_flag=True, help="Show verbose output")
def test(verbose):
    """Run a test to confirm the CLI is working correctly."""
    if verbose:
        cprint("UCLA CLI test command executed successfully!", "green")
        cprint("Available commands:", "blue")
        cprint("- classes: Search for classes offered in a term", "cyan")
        cprint("- rooms: Get information about rooms", "cyan")
        cprint("- test: Run this test command", "cyan")
    else:
        cprint("UCLA CLI is working correctly!", "green")


if __name__ == "__main__":
    ucla()