# ucla_cli/section_details.py
import requests
from bs4 import BeautifulSoup, Tag
import click
import time
import random
import re

REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_page_content(url, retries=3, delay=2):
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            click.echo(click.style(f"Attempt {attempt + 1} failed to fetch {url}: {e}", fg='red'))
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                click.echo(click.style(f"All retries failed for {url}.", fg='red'))
                return None

def get_data_for_title(scope_element, title_str_exact):
    title_p = scope_element.find('p', class_='class_detail_title', string=lambda t: t and t.strip() == title_str_exact)
    
    if title_p:
        data_element = None
        
        if title_str_exact == "Class Notes":
            next_sibling = title_p.find_next_sibling()
            if next_sibling:
                if next_sibling.name == 'ul':
                    data_element = next_sibling
                elif next_sibling.name == 'p' and 'section_data' in next_sibling.get('class', []):
                    if not next_sibling.get_text(strip=True):
                        ul_after_empty_p = next_sibling.find_next_sibling('ul')
                        if ul_after_empty_p:
                            data_element = ul_after_empty_p
                        else: 
                            data_element = next_sibling 
                    else: 
                        data_element = next_sibling
        
        if not data_element:
            data_element = title_p.find_next_sibling('p', class_=lambda x: x and 'section_data' in x)
        
        if data_element:
            if data_element.name == 'ul':
                list_items_texts = []
                for li in data_element.find_all('li', recursive=False):
                    li_text_parts = []
                    for content_item in li.contents:
                        if isinstance(content_item, str):
                            li_text_parts.append(content_item.strip())
                        elif isinstance(content_item, Tag) and content_item.name == 'a':
                            href = content_item.get('href', '')
                            link_text = content_item.get_text(strip=True)
                            link_representation = link_text
                            if href:
                                link_representation += f" [{href}]"
                            li_text_parts.append(link_representation.strip())
                        elif isinstance(content_item, Tag): # Other tags within li
                            li_text_parts.append(content_item.get_text(strip=True))
                    
                    full_li_text = " ".join(part for part in li_text_parts if part)
                    if full_li_text:
                        list_items_texts.append(full_li_text)
                text_content = " ; ".join(list_items_texts)
            else:
                text_content = data_element.get_text(separator=' ', strip=True)
            
            return text_content if text_content else "N/A (Empty)"
            
    return "N/A"


def extract_section_details_from_url(section_url):
    default_details = {
        "course_description": "N/A",
        "class_description_detail": "N/A",
        "general_education_ge": "N/A",
        "writing_ii_requirement": "N/A",
        "diversity_info": "N/A",
        "class_notes": "N/A"
    }
    
    if not section_url:
        default_details["course_description"] = "N/A (No URL provided)"
        return default_details

    click.echo(click.style(f"Fetching section details from: {section_url}", fg='magenta'))
    time.sleep(random.uniform(0.5, 1.5)) 

    html_content = get_page_content(section_url)
    if not html_content:
        default_details["course_description"] = "N/A (Failed to fetch page content)"
        return default_details

    soup = BeautifulSoup(html_content, 'html.parser')
    content_to_parse = soup 
    
    template_tag = soup.find('template', id='ucla-sa-soc-app')
    if template_tag:
        click.echo(click.style(f"Found <template id='ucla-sa-soc-app'>. Parsing its content.", fg='blue'))
        template_html_string = template_tag.decode_contents()
        if template_html_string:
            content_to_parse = BeautifulSoup(template_html_string, 'html.parser')
        else:
            click.echo(click.style(f"<template id='ucla-sa-soc-app'> found but has empty content. Will search in main document.", fg='yellow'))
    else:
        click.echo(click.style(f"No <template id='ucla-sa-soc-app'> found. Parsing main document.", fg='yellow'))

    section_div = content_to_parse.find('div', id='section')
    
    if not section_div and content_to_parse is not soup:
        click.echo(click.style(f"div#section not found in <template> content. Trying in main document again...", fg='yellow'))
        section_div = soup.find('div', id='section')
    
    if not section_div:
        click.echo(click.style(f"Could not find 'div#section' on page: {section_url}", fg='red'))
        return default_details

    details = default_details.copy()

    details["course_description"] = get_data_for_title(section_div, "Course Description")
    details["class_description_detail"] = get_data_for_title(section_div, "Class Description")
    details["general_education_ge"] = get_data_for_title(section_div, "General Education (GE)")
    details["writing_ii_requirement"] = get_data_for_title(section_div, "Writing II")
    details["diversity_info"] = get_data_for_title(section_div, "Diversity")
    details["class_notes"] = get_data_for_title(section_div, "Class Notes")
    
    return details