import argparse
import json
import os
import logging
import re
from typing import Dict, Optional, Union
from datasets import load_dataset
from tqdm import tqdm
from openai import OpenAI

# Enhanced logging configuration
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('math500_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI(api_key="optillm", base_url="http://localhost:8000/v1")
# client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url="https://openrouter.ai/api/v1")

SYSTEM_PROMPT = '''You are solving mathematics problems.

Please think step by step.

Important: Always end your solution with the final answer in this format:

\\[
\\boxed{your_answer_here}
\\]

Make sure to write your complete answer inside the \\boxed{} command.'''

def load_math500_dataset() -> list[dict]:
    """
    Load the MATH-500 dataset.
    Returns:
        list[dict]: The dataset of problems.
    """
    dataset = load_dataset("HuggingFaceH4/MATH-500")
    dataset = dataset["test"]
    logging.debug(f"Dataset size: {len(dataset)}.")
    return dataset

def extract_answer(response: str) -> Optional[str]:
    """Extract the answer from a math solution response."""
    if not response:
        logger.debug("Empty response received")
        return None
    
    # Find the last \boxed{...} in the response
    start_idx = response.rfind('\\boxed{')
    if start_idx == -1:
        logger.debug("No \\boxed{} found in response")
        return None
        
    # Find the matching closing brace
    brace_count = 1
    pos = start_idx + 7  # length of '\boxed{'
    
    while pos < len(response) and brace_count > 0:
        if response[pos] == '{':
            brace_count += 1
        elif response[pos] == '}':
            brace_count -= 1
        pos += 1
    
    if brace_count == 0:
        answer = response[start_idx + 7:pos - 1]
        logger.debug(f"Extracted answer: {answer}")
        return answer.strip()
    
    logger.debug("No matching closing brace found")
    return None

def normalize_number(num_str: str) -> str:
    """Helper function to normalize number representation."""
    logger.debug(f"Normalizing number: {repr(num_str)}")
    try:
        # Remove commas, currency symbols, units, and whitespace
        cleaned = re.sub(r'[,\$\\]|\s*(?:cm|m|kg|ft|in|lb|oz|ml|L)$', '', num_str).strip()
        # Convert to float and back to handle scientific notation if present
        num = float(cleaned)
        if 1e-5 <= abs(num) <= 1e6:
            # For regular numbers, remove trailing zeros after decimal
            # For currency, keep two decimal places
            if '.' in num_str:  # If original had decimal, keep decimal format
                result = f"{num:.2f}"
            else:
                result = f"{num:f}".rstrip('0').rstrip('.')
        else:
            # For very large/small numbers, use scientific notation
            result = f"{num:e}".lower()
        logger.debug(f"Normalized number result: {repr(result)}")
        return result
    except:
        logger.debug(f"Failed to normalize number, returning original: {repr(num_str)}")
        return num_str
    
def normalize_fraction(fraction_str: str) -> str:
    """Helper function to normalize fractions."""
    logger.debug(f"Normalizing fraction: {repr(fraction_str)}")
    try:
        # Convert division to fraction
        if '/' in fraction_str and not any(c in fraction_str for c in '\\{}'):
            num, den = fraction_str.split('/')
            return f"\\frac{{{num.strip()}}}{{{den.strip()}}}"
            
        # Remove \dfrac or \frac prefix
        fraction_str = fraction_str.replace('\\dfrac', '\\frac')
        
        # Match the numerator and denominator
        match = re.match(r'^\\frac\{([^{}]+)\}\{([^{}]+)\}$', fraction_str)
        if match:
            num, den = match.groups()
            # Normalize both parts
            norm_num = normalize_answer(num)
            norm_den = normalize_answer(den)
            if norm_num and norm_den:
                result = f"\\frac{{{norm_num}}}{{{norm_den}}}"
                logger.debug(f"Normalized fraction result: {repr(result)}")
                return result
    except:
        logger.debug(f"Failed to normalize fraction, returning original: {repr(fraction_str)}")
    return fraction_str

def normalize_matrix_entry(entry: str) -> str:
    """Helper function to normalize a single matrix entry."""
    logger.debug(f"Normalizing matrix entry: {repr(entry)}")
    entry = entry.strip()
    
    # If it's a fraction (either \frac or normal division)
    if '\\frac' in entry or '\\dfrac' in entry or '/' in entry:
        return normalize_fraction(entry)
    
    # Otherwise normalize as regular answer
    return normalize_answer(entry) or entry

def normalize_matrix(matrix_str: str) -> str:
    """Helper function to normalize matrices and vectors."""
    logger.debug(f"Normalizing matrix: {repr(matrix_str)}")
    try:
        # Remove all whitespace first
        matrix_str = ''.join(matrix_str.split())
        
        # Extract the matrix content
        match = re.match(r'^\\begin\{pmatrix\}(.*?)\\end\{pmatrix\}$', matrix_str)
        if not match:
            return matrix_str
            
        content = match.group(1)
        
        # Split into rows
        rows = content.split('\\\\')
        
        # Normalize each entry in each row
        normalized_rows = []
        for row in rows:
            entries = [normalize_matrix_entry(entry) for entry in row.split('&')] if '&' in row else [normalize_matrix_entry(row)]
            normalized_rows.append('&'.join(entries))
        
        # Reconstruct the matrix
        result = r"\begin{pmatrix}" + ' \\\\ '.join(normalized_rows) + r"\end{pmatrix}"
        logger.debug(f"Normalized matrix result: {repr(result)}")
        return result
        
    except Exception as e:
        logger.debug(f"Failed to normalize matrix: {str(e)}")
        return matrix_str

def normalize_algebraic_expression(expr: str) -> str:
    """Helper function to normalize algebraic expressions."""
    logger.debug(f"Normalizing algebraic expression: {repr(expr)}")
    try:
        # Remove all whitespace
        expr = ''.join(expr.split())
        
        # Handle simple monomial with exponent (e.g., 5r^5)
        monomial_match = re.match(r'^(-?\d*\.?\d*)?([a-zA-Z])(?:\^(-?\d+))?$', expr)
        if monomial_match:
            coeff, var, exp = monomial_match.groups()
            coeff = coeff if coeff and coeff not in ['+', '-'] else ('1' if not coeff else '-1')
            exp = exp if exp else '1'
            if coeff == '1' and exp == '1':
                result = var
            elif coeff == '1':
                result = f"{var}^{exp}"
            elif coeff == '-1' and exp == '1':
                result = f"-{var}"
            elif coeff == '-1':
                result = f"-{var}^{exp}"
            elif exp == '1':
                result = f"{coeff}{var}"
            else:
                result = f"{coeff}{var}^{exp}"
            logger.debug(f"Matched as monomial with exponent: {repr(result)}")
            return result.lower()
            
        # Special case: If it's a single term with π
        pi_term_match = re.match(r'^(-?\d*\.?\d*)\\?pi$', expr)
        if pi_term_match:
            coeff = pi_term_match.group(1)
            if not coeff or coeff == '-':
                coeff = '-1' if coeff == '-' else '1'
            return f"{coeff}\\pi"
            
        # Handle fractions with π
        frac_pi_match = re.match(r'^\\frac{([^{}]+)}{([^{}]+)}\\?pi$', expr)
        if frac_pi_match:
            num, den = frac_pi_match.groups()
            return f"\\frac{{{num}}}{{{den}}}\\pi"
        
        # Handle basic fractions
        frac_match = re.match(r'^\\frac{([^{}]+)}{([^{}]+)}$', expr)
        if frac_match:
            num, den = frac_match.groups()
            return f"\\frac{{{num}}}{{{den}}}"
        
        # Split into terms (handle both + and -)
        terms = []
        current_term = ""
        for i, char in enumerate(expr):
            if char in ['+', '-'] and i > 0:
                if current_term:
                    terms.append(current_term)
                current_term = char
            else:
                current_term += char
        if current_term:
            terms.append(current_term)
        
        # If it's just a single number, return normalized version
        if len(terms) == 1 and re.match(r'^-?[\d,]+$', terms[0]):
            return normalize_number(terms[0])
            
        # Process each term and sort
        processed_terms = []
        for term in terms:
            # Handle leading + if present
            if term.startswith('+'):
                term = term[1:]
                
            # Add implicit + for positive terms
            if not term.startswith('-'):
                term = '+' + term
                
            # Separate coefficient and variable parts
            match = re.match(r'^([+-])?\s*(\d*\.?\d*)?([a-zA-Z](?:\^\d+)?)?$', term)
            if match:
                sign, coeff, var = match.groups()
                # Handle default coefficients
                if not coeff and var:
                    coeff = '1'
                elif not coeff:
                    coeff = '0'
                # Create standardized term
                processed_terms.append((sign, float(coeff), var or ''))
        
        # Sort terms: variables first (in alphabetical order), then constants
        processed_terms.sort(key=lambda x: (not bool(x[2]), x[2], -x[1]))
        
        # Reconstruct the expression
        result = ""
        for sign, coeff, var in processed_terms:
            if coeff == 0:
                continue
            term = ""
            if coeff == 1 and var:
                term = var
            elif coeff == -1 and var:
                term = f"-{var}"
            elif var:
                term = f"{coeff}{var}"
            else:
                term = str(coeff)
            
            if result and term[0] != '-':
                result += '+'
            result += term
        
        logger.debug(f"Normalized algebraic expression result: {repr(result)}")
        return result.lower()
    except Exception as e:
        logger.debug(f"Failed to normalize algebraic expression: {str(e)}")
        return expr.lower()  # Return lowercased original if normalization fails
    
def normalize_interval_bound(bound: str) -> str:
    """Helper function to normalize interval bounds."""
    logger.debug(f"Normalizing interval bound: {repr(bound)}")
    
    # Handle infinity
    if '\\infty' in bound:
        sign = '-' if bound.startswith('-') else ''
        return f"{sign}\\infty"
        
    # For other bounds, use regular answer normalization
    return normalize_answer(bound) or bound

def normalize_interval(interval_str: str) -> str:
    """Helper function to normalize intervals."""
    logger.debug(f"Normalizing interval: {repr(interval_str)}")
    try:
        # Remove all whitespace first
        interval_str = ''.join(interval_str.split())
        
        # Extract the interval content, handling \left and \right
        match = re.match(r'^\\left?([[(])(.*?),(.+?)\\right?([)\]])$', interval_str)
        if not match:
            # Try without \left and \right
            match = re.match(r'^([[(])(.*?),(.+?)([)\]])$', interval_str)
            if not match:
                return interval_str
                
        left_bracket, left_bound, right_bound, right_bracket = match.groups()
        
        # Normalize each bound
        norm_left = normalize_interval_bound(left_bound)
        norm_right = normalize_interval_bound(right_bound)
        
        # Reconstruct the interval
        result = f"\\left{left_bracket}{norm_left},{norm_right}\\right{right_bracket}"
        logger.debug(f"Normalized interval result: {repr(result)}")
        return result
        
    except Exception as e:
        logger.debug(f"Failed to normalize interval: {str(e)}")
        return interval_str
    
def normalize_ordered_tuple(tuple_str: str) -> str:
    """Helper function to normalize ordered tuples/lists of numbers."""
    logger.debug(f"Normalizing tuple: {repr(tuple_str)}")
    try:
        # Remove outer parentheses and split by commas
        parts = tuple_str.strip('()').split(',')
        # Normalize each part and rejoin
        normalized_parts = []
        for part in parts:
            norm_part = normalize_answer(part.strip())
            if not norm_part:  # If any part fails to normalize, return None
                return None
            normalized_parts.append(norm_part)
        result = f"({','.join(normalized_parts)})"
        logger.debug(f"Normalized tuple result: {repr(result)}")
        return result
    except:
        logger.debug(f"Failed to normalize tuple, returning None")
        return None

def normalize_answer(answer: str) -> str:
    """Normalize the answer string for comparison."""
    logger.debug(f"Normalizing answer: {repr(answer)}")
    
    if answer is None:
        logger.debug("Received None answer")
        return ""
        
    # Remove all whitespace
    answer = ''.join(answer.split())
    logger.debug(f"After whitespace removal: {repr(answer)}")
    
    if not answer:
        logger.debug("Answer became empty after whitespace removal")
        return None
    
    # Log each character and its ASCII value for debugging
    char_codes = [(c, ord(c)) for c in answer]
    logger.debug(f"Character codes: {char_codes}")

    # Handle intervals first (with or without \left and \right)
    if (answer.startswith('\\left[') or answer.startswith('\\left(') or 
        answer.startswith('[') or answer.startswith('(')) and \
       (answer.endswith('\\right]') or answer.endswith('\\right)') or 
        answer.endswith(']') or answer.endswith(')')):
        result = normalize_interval(answer)
        if result:
            logger.debug(f"Matched as interval: {repr(result)}")
            return result
    
    # Handle matrices/vectors
    if answer.startswith('\\begin{pmatrix}') and answer.endswith('\\end{pmatrix}'):
        result = normalize_matrix(answer)
        if result:
            logger.debug(f"Matched as matrix: {repr(result)}")
            return result
    
    # Normalize all fraction commands to \frac first
    answer = answer.replace('\\dfrac', '\\frac')

    # Handle fractions (both \frac and \dfrac)
    if '\\frac' in answer or '\\dfrac' in answer or '/' in answer:
        result = normalize_fraction(answer)
        if result:
            logger.debug(f"Matched as fraction: {repr(result)}")
            return result

    # Handle ordered tuples first (including pairs and longer tuples)
    if answer.startswith('(') and answer.endswith(')'):
        result = normalize_ordered_tuple(answer)
        if result:
            logger.debug(f"Matched as ordered tuple: {repr(result)}")
            return result

    # Handle square roots
    sqrt_match = re.match(r'^(-)?\\sqrt\{?(\d+)\}?$', answer)
    if sqrt_match:
        sign, num = sqrt_match.groups()
        sign = sign if sign else ''
        result = f"{sign}\\sqrt{{{num}}}"
        logger.debug(f"Matched as square root: {repr(result)}")
        return result
    
    # Handle numbers with base subscripts
    base_match = re.match(r'^(\d+)(?:_\{?(\d+)\}?|_(\d+))$', answer)
    if base_match:
        number, base1, base2 = base_match.groups()
        base = base1 if base1 else base2
        result = f"{number}_{base}"
        logger.debug(f"Matched as base number: {repr(result)}")
        return result

    # Handle numbers with percentage sign first
    percent_match = re.match(r'^(\d+(?:\.\d*)?)\s*\\?%$', answer)
    if percent_match:
        number = percent_match.group(1)
        result = normalize_number(number)
        logger.debug(f"Matched as percentage: {repr(result)}")
        return result
    
    # Handle numbers with units (including LaTeX spaces and comma-separated units)
    unit_match = re.match(r'^(\d+(?:\.\d*)?)\s*(?:(?:\\[,\s])|,)?\s*(?:\\\\)?(?:\\text{(\w+)}|\\?(?:cm|m|kg|ft|in|lb|oz|ml|L))$', answer)
    if unit_match:
        number = unit_match.group(1)
        result = normalize_number(number)
        logger.debug(f"Matched as number with unit: {repr(result)}")
        return result
    
    # Try to handle currency values first
    currency_match = re.match(r'^\\?\$?([\d,]+\.?\d*)$', answer)
    if currency_match:
        result = normalize_number(currency_match.group(1))
        logger.debug(f"Matched as currency: {repr(result)}")
        return result
    
    # Try to handle pure numbers with commas first
    if re.match(r'^-?[\d,]+$', answer):
        result = normalize_number(answer)
        logger.debug(f"Matched as number: {repr(result)}")
        return result
    
    # Try to extract numeric value with optional units
    unit_match = re.match(r'^(-?[\d,]+(?:\.\d*)?)\s*(?:\\(?:mbox|text|hbox|displaystyle)\{[^}]+\})?(?:\^?\d)?$', answer)
    if unit_match:
        result = normalize_number(unit_match.group(1))
        logger.debug(f"Matched as number with units: {repr(result)}")
        return result
    
    # Handle multiple choice answers
    mc_match = re.match(r'^\\text{\(?([A-Za-z])\)?}$|^\(?([A-Za-z])\)?$', answer)
    if mc_match:
        result = (mc_match.group(1) or mc_match.group(2)).lower()
        logger.debug(f"Matched as multiple choice: {repr(result)}")
        return result
    
    # Handle degrees
    degree_match = re.match(r'^(-?[\d,]+(?:\.\d*)?)\s*(?:(?:\^?\\circ)|(?:{\\circ})|(?:°))?$', answer)
    if degree_match:
        result = normalize_number(degree_match.group(1))
        logger.debug(f"Matched as degrees: {repr(result)}")
        return result
    
    # Remove \text{} command without changing content FIRST
    answer = re.sub(r'\\text{([^{}]+)}', r'\1', answer)
    logger.debug(f"After \\text removal: {repr(answer)}")
    
    # Try to handle algebraic expressions
    try:
        result = normalize_algebraic_expression(answer)
        logger.debug(f"Normalized as algebraic expression: {repr(result)}")
        return result
    except:
        logger.debug("Failed to normalize as algebraic expression")
        pass
    
    # Remove \left and \right commands
    answer = answer.replace('\\left', '').replace('\\right', '')
    
    # Remove any remaining extra backslashes before common symbols
    answer = answer.replace('\\(', '(').replace('\\)', ')')
    answer = answer.replace('\\[', '[').replace('\\]', ']')
    answer = answer.replace('\\{', '{').replace('\\}', '}')
    
    # Normalize square roots
    answer = re.sub(r'\\sqrt{(\d+)}', r'\\sqrt\1', answer)
    answer = re.sub(r'\\sqrt{([^{}]+)}', r'\\sqrt\1', answer)
    
    # Handle percentage notation
    if re.match(r'^\d+\\%$', answer) or re.match(r'^\d+$', answer):
        answer = re.sub(r'\\%$', '', answer)
    
    # Handle \text{} command again in case it was nested
    answer = re.sub(r'\\text{([^{}]+)}', r'\1', answer)
    
    # Strip unnecessary outer braces
    while len(answer) >= 2 and answer[0] == '{' and answer[-1] == '}':
        if '\\frac' in answer:
            break
        answer = answer[1:-1]
    
    result = answer.lower()
    logger.debug(f"Final normalized result: {repr(result)}")
    return result if result else None

def compare_answers(correct_answer: str, predicted_answer: Optional[str]) -> bool:
    """Compare the correct answer with the predicted answer."""
    logger.debug(f"Comparing answers - Correct: {repr(correct_answer)}, Predicted: {repr(predicted_answer)}")
    
    if predicted_answer is None:
        logger.debug("Predicted answer is None")
        return False
        
    normalized_correct = normalize_answer(correct_answer)
    normalized_predicted = normalize_answer(predicted_answer)
    
    logger.debug(f"Normalized answers - Correct: {repr(normalized_correct)}, Predicted: {repr(normalized_predicted)}")
    
    # If either normalization returns None or empty string, answers don't match
    if not normalized_correct or not normalized_predicted:
        logger.debug("One or both normalized answers are None or empty")
        return False
        
    # If both answers became empty strings, they don't match
    if normalized_correct == "" and normalized_predicted == "":
        logger.debug("Both answers normalized to empty strings")
        return False
    
    # For intervals, they must match exactly (including brackets)
    if ('\\left[' in normalized_correct or '\\left(' in normalized_correct) and \
       ('\\left[' in normalized_predicted or '\\left(' in normalized_predicted):
        result = normalized_correct == normalized_predicted
        logger.debug(f"Interval comparison result: {result}")
        return result
    
    result = normalized_correct == normalized_predicted
    logger.debug(f"Comparison result: {result}")
    return result

def get_llm_response(problem: str, model: str) -> str:
    """
    Get response from the LLM for a given problem.
    
    Args:
        problem (str): The problem text
        model (str): The model identifier
        
    Returns:
        str: Model's response
    """
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.6,  # Lower temperature for more consistent answers
            messages=[
                {"role": "user", "content": SYSTEM_PROMPT + "\n" + problem}
            ],
            max_tokens=4096, # for thinking models, we need to use a lot more tokens
            # extra_body = {
            #     "decoding" : "thinkdeeper",
            # }
        )
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        logger.error(f"Error getting LLM response: {e}")
        return ""

def load_existing_results(filename: str) -> list[Dict]:
    """Load existing results from file if it exists."""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_result(filename: str, result: Dict):
    """Save a single result to the results file."""
    results = load_existing_results(filename)
    results.append(result)
    with open(filename, 'w') as f:
        json.dump(results, f, indent=2)

def analyze_results(results: list[Dict]):
    """
    Analyze and print summary statistics of the results.
    
    Args:
        results (list[Dict]): List of evaluation results
    """
    total = len(results)
    correct = sum(1 for r in results if r['is_correct'])
    accuracy = correct / total if total > 0 else 0
    
    print("\n=== Results Summary ===")
    print(f"Total problems: {total}")
    print(f"Correct answers: {correct}")
    print(f"Accuracy: {accuracy:.2%}")
    
    print("\n=== Incorrect Problems ===")
    for r in results:
        if not r['is_correct']:
            print(f"Problem {r['index']}:")
            print(f"Expected: {r['correct_answer']}")
            print(f"Predicted: {r['predicted_answer']}")
            print("---")

def main(model: str):
    """Main evaluation function."""
    os.makedirs("results", exist_ok=True)
    results_file = f"evaluation_results_math500_{model.replace('/', '_')}.json"
    
    dataset = load_math500_dataset()
    existing_results = load_existing_results(results_file)
    
    # Create a set of already processed indexes for efficient lookup
    processed_indexes = {result['index'] for result in existing_results}
    
    for idx, item in enumerate(tqdm(dataset, desc="Evaluating problems")):
        # Skip if this index has already been processed
        if idx in processed_indexes:
            continue
            
        problem_text = item['problem']
        correct_answer = item['answer']
        
        # Get model's response
        response = get_llm_response(problem_text, model)
        predicted_answer = extract_answer(response)

        # Compare answers using the new comparison function
        is_correct = compare_answers(correct_answer, predicted_answer)
        
        result = {
            "index": idx,
            "problem": problem_text,
            "response": response,
            "correct_answer": correct_answer,
            "predicted_answer": predicted_answer,
            "is_correct": is_correct
        }
        save_result(results_file, result)
    
    final_results = load_existing_results(results_file)
    analyze_results(final_results)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate LLM performance on MATH-500 problems")
    parser.add_argument("--model", type=str, required=True, help="OpenAI model to use (e.g., gpt-4, gpt-3.5-turbo)")
    args = parser.parse_args()
    
    main(args.model)
